# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import json
import logging
import socket

from telemetry import inspector_console
from telemetry import inspector_page
from telemetry import inspector_runtime
from telemetry import inspector_timeline
from telemetry import png_bitmap
from telemetry import tab_crash_exception
from telemetry import util
from telemetry import websocket

class InspectorException(Exception):
  pass

class TabBackend(object):
  def __init__(self, browser, browser_backend, debugger_url):
    assert debugger_url
    self._browser = browser
    self._browser_backend = browser_backend
    self._debugger_url = debugger_url
    self._socket = None
    self._next_request_id = 0
    self._domain_handlers = {}
    self._cur_socket_timeout = 0

    self.__console = None
    self.__page = None
    self.__runtime = None
    self.__timeline = None

  def __del__(self):
    self._Disconnect()

  def _Connect(self):
    if self._socket:
      return
    self._socket = websocket.create_connection(self._debugger_url)

    self.__console = inspector_console.InspectorConsole(self)
    self.__page = inspector_page.InspectorPage(self)
    self.__runtime = inspector_runtime.InspectorRuntime(self)
    self.__timeline = inspector_timeline.InspectorTimeline(self)

  def _Disconnect(self):
    for _, handlers in self._domain_handlers.items():
      _, will_close_handler = handlers
      will_close_handler()
    self._domain_handlers = {}

    if self._socket:
      self._socket.close()
      self._socket = None

    self.__console = None
    self.__page = None
    self.__runtime = None
    self.__timeline = None

  @property
  def _console(self):
    self._Connect()
    return self.__console

  @property
  def _page(self):
    self._Connect()
    return self.__page

  @property
  def _runtime(self):
    self._Connect()
    return self.__runtime

  @property
  def _timeline(self):
    self._Connect()
    return self.__timeline

  # General public methods.

  @property
  def browser(self):
    return self._browser

  @property
  def url(self):
    self._Disconnect()
    return self._browser_backend.tabs.GetTabUrl(self._debugger_url)

  def Activate(self):
    self._Connect()
    self._browser_backend.tabs.ActivateTab(self._debugger_url)

  def Close(self):
    self._Disconnect()
    self._browser_backend.tabs.CloseTab(self._debugger_url)

  # Public methods implemented in JavaScript.

  def WaitForDocumentReadyStateToBeComplete(self, timeout):
    util.WaitFor(
        lambda: self.__runtime.Evaluate('document.readyState') == 'complete',
        timeout)

  def WaitForDocumentReadyStateToBeInteractiveOrBetter(
      self, timeout):
    def IsReadyStateInteractiveOrBetter():
      rs = self.__runtime.Evaluate('document.readyState')
      return rs == 'complete' or rs == 'interactive'
    util.WaitFor(IsReadyStateInteractiveOrBetter, timeout)

  @property
  def screenshot_supported(self):
    if self.__runtime.Evaluate(
        'window.chrome.gpuBenchmarking === undefined'):
      return False

    if self.__runtime.Evaluate(
        'window.chrome.gpuBenchmarking.windowSnapshotPNG === undefined'):
      return False

    return True

  def Screenshot(self, timeout):
    if self.__runtime.Evaluate(
        'window.chrome.gpuBenchmarking === undefined'):
      raise Exception("Browser was not started with --enable-gpu-benchmarking")

    if self.__runtime.Evaluate(
        'window.chrome.gpuBenchmarking.beginWindowSnapshotPNG === undefined'):
      raise Exception("Browser does not support window snapshot API.")

    self.__runtime.Evaluate("""
        if(!window.__telemetry) {
          window.__telemetry = {}
        }
        window.__telemetry.snapshotComplete = false;
        window.__telemetry.snapshotData = null;
        window.chrome.gpuBenchmarking.beginWindowSnapshotPNG(
          function(snapshot) {
            window.__telemetry.snapshotData = snapshot;
            window.__telemetry.snapshotComplete = true;
          }
        );
    """)

    def IsSnapshotComplete():
      return self.__runtime.Evaluate('window.__telemetry.snapshotComplete')

    util.WaitFor(IsSnapshotComplete, timeout)

    snap = self.__runtime.Evaluate("""
      (function() {
        var data = window.__telemetry.snapshotData;
        delete window.__telemetry.snapshotComplete;
        delete window.__telemetry.snapshotData;
        return data;
      })()
    """)
    if snap:
      return png_bitmap.PngBitmap(snap['data'])
    return None

  # Console public methods.

  @property
  def message_output_stream(self):  # pylint: disable=E0202
    return self.__console.message_output_stream

  @message_output_stream.setter
  def message_output_stream(self, stream):  # pylint: disable=E0202
    self.__console.message_output_stream = stream

  # Page public methods.

  def PerformActionAndWaitForNavigate(self, action_function, timeout):
    self.__page.PerformActionAndWaitForNavigate(action_function, timeout)

  def Navigate(self, url, timeout):
    self.__page.Navigate(url, timeout)

  def GetCookieByName(self, name, timeout):
    return self.__page.GetCookieByName(name, timeout)

  # Runtime public methods.

  def ExecuteJavascript(self, expr, timeout):
    self.__runtime.Execute(expr, timeout)

  def EvaluateJavascript(self, expr, timeout):
    return self.__runtime.Evaluate(expr, timeout)

  # Timeline public methods.

  @property
  def timeline_events(self):
    return self.__timeline.timeline_events

  def StartTimelineRecording(self):
    self.__timeline.Start()

  def StopTimelineRecording(self):
    self.__timeline.Stop()

  # Methods used internally by other backends.

  def DispatchNotifications(self, timeout=10):
    self._SetTimeout(timeout)
    try:
      data = self._socket.recv()
    except (socket.error, websocket.WebSocketException):
      if self._browser_backend.tabs.DoesDebuggerUrlExist(self._debugger_url):
        return
      raise tab_crash_exception.TabCrashException()

    res = json.loads(data)
    logging.debug('got [%s]', data)
    if 'method' in res:
      self._HandleNotification(res)

  def _HandleNotification(self, res):
    mname = res['method']
    dot_pos = mname.find('.')
    domain_name = mname[:dot_pos]
    if domain_name in self._domain_handlers:
      try:
        self._domain_handlers[domain_name][0](res)
      except Exception:
        import traceback
        traceback.print_exc()
    else:
      logging.debug('Unhandled inspector message: %s', res)

  def SendAndIgnoreResponse(self, req):
    req['id'] = self._next_request_id
    self._next_request_id += 1
    data = json.dumps(req)
    self._socket.send(data)
    logging.debug('sent [%s]', data)

  def _SetTimeout(self, timeout):
    if self._cur_socket_timeout != timeout:
      self._socket.settimeout(timeout)
      self._cur_socket_timeout = timeout

  def SyncRequest(self, req, timeout=10):
    # TODO(nduca): Listen to the timeout argument
    # pylint: disable=W0613
    self._SetTimeout(timeout)
    self.SendAndIgnoreResponse(req)

    while True:
      try:
        data = self._socket.recv()
      except (socket.error, websocket.WebSocketException):
        if self._browser_backend.tabs.DoesDebuggerUrlExist(self._debugger_url):
          raise util.TimeoutException(
            'Timed out waiting for reply. This is unusual.')
        raise tab_crash_exception.TabCrashException()

      res = json.loads(data)
      logging.debug('got [%s]', data)
      if 'method' in res:
        self._HandleNotification(res)
        continue

      if res['id'] != req['id']:
        logging.debug('Dropped reply: %s', json.dumps(res))
        continue
      return res

  def RegisterDomain(self,
      domain_name, notification_handler, will_close_handler):
    """Registers a given domain for handling notification methods.

    For example, given tab_backend:
       def OnConsoleNotification(msg):
          if msg['method'] == 'Console.messageAdded':
             print msg['params']['message']
          return
       def OnConsoleClose(self):
          pass
       tab_backend.RegisterDomain('Console',
                                        OnConsoleNotification, OnConsoleClose)
       """
    assert domain_name not in self._domain_handlers
    self._domain_handlers[domain_name] = (notification_handler,
                                          will_close_handler)

  def UnregisterDomain(self, domain_name):
    """Unregisters a previously registered domain."""
    assert domain_name in self._domain_handlers
    self._domain_handlers.pop(domain_name)
