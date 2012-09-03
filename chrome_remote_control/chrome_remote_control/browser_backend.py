# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import urllib2
import json

import browser_finder
import inspector_backend
import tab
import util

class BrowserGoneException(Exception):
  pass

class BrowserBackend(object):
  """A base class for broser backends. Provides basic functionality
  once a remote-debugger port has been established."""
  def __init__(self):
    pass

  def __del__(self):
    self.Close()

  def _WaitForBrowserToComeUp(self):
    def IsBrowserUp():
      try:
        self._ListTabs()
      except urllib2.URLError:
        if not self.IsBrowserRunning():
          raise BrowserGoneException()
        return False
      else:
        return True
    util.WaitFor(IsBrowserUp)

  def _ListTabs(self, timeout=None):
    if timeout:
      req = urllib2.urlopen("http://localhost:%i/json" % self._port,
                            timeout=timeout)
    else:
      req = urllib2.urlopen("http://localhost:%i/json" % self._port)
    data = req.read()
    return json.loads(data)

  @property
  def num_tabs(self):
    return len(self._ListTabs())

  def GetNthTabUrl(self, index):
    return self._ListTabs()[index]["url"]

  def ConnectToNthTab(self, index):
    ib = inspector_backend.InspectorBackend(self, self._ListTabs()[index])
    return tab.Tab(ib)
