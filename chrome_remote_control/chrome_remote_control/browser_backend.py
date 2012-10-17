# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import urllib2
import httplib
import socket
import json

from chrome_remote_control import browser_gone_exception
from chrome_remote_control import inspector_backend
from chrome_remote_control import tab
from chrome_remote_control import util
from chrome_remote_control import wpr_modes
from chrome_remote_control import wpr_server

class BrowserBackend(object):
  """A base class for broser backends. Provides basic functionality
  once a remote-debugger port has been established."""
  def __init__(self, is_content_shell, options):
    self.is_content_shell = is_content_shell
    self.options = options
    self._port = None

  def GetBrowserStartupArgs(self):
    args = []
    args.extend(self.options.extra_browser_args)
    args.append('--disable-background-networking')
    args.append('--no-first-run')
    if self.options.wpr_mode != wpr_modes.WPR_OFF:
      args.extend(wpr_server.CHROME_FLAGS)
    return args

  @property
  def wpr_mode(self):
    return self.options.wpr_mode

  def _WaitForBrowserToComeUp(self):
    def IsBrowserUp():
      try:
        self._ListTabs()
      except socket.error:
        if not self.IsBrowserRunning():
          raise browser_gone_exception.BrowserGoneException()
        return False
      except httplib.BadStatusLine:
        if not self.IsBrowserRunning():
          raise browser_gone_exception.BrowserGoneException()
        return False
      except urllib2.URLError:
        if not self.IsBrowserRunning():
          raise browser_gone_exception.BrowserGoneException()
        return False
      else:
        return True
    try:
      util.WaitFor(IsBrowserUp, timeout=30)
    except util.TimeoutException:
      raise browser_gone_exception.BrowserGoneException()

  def _ListTabs(self, timeout=None):
    if timeout:
      req = urllib2.urlopen('http://localhost:%i/json' % self._port,
                            timeout=timeout)
    else:
      req = urllib2.urlopen('http://localhost:%i/json' % self._port)
    data = req.read()
    all_contexts = json.loads(data)
    tabs = [ctx for ctx in all_contexts
            if not ctx['url'].startswith('chrome-extension://')]
    return tabs

  @property
  def num_tabs(self):
    return len(self._ListTabs())

  def GetNthTabUrl(self, index):
    return self._ListTabs()[index]['url']

  def ConnectToNthTab(self, browser, index):
    ib = inspector_backend.InspectorBackend(self, self._ListTabs()[index])
    return tab.Tab(browser, ib)

  def DoesDebuggerUrlExist(self, url):
    matches = [t for t in self._ListTabs()
               if t['webSocketDebuggerUrl'] == url]
    return len(matches) >= 1

  def CreateForwarder(self, host_port):
    raise NotImplementedError()

  def IsBrowserRunning(self):
    raise NotImplementedError()
