# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

class WaitUntil(object):

  def __init__(self, previous_action, attributes=None):
    assert previous_action is not None, 'wait_until must have a previous action'
    self.timeout = 60
    if attributes:
      for k, v in attributes.iteritems():
        setattr(self, k, v)
    self._previous_action = previous_action

  def RunActionAndWait(self, tab):
    if getattr(self, 'condition', None) == 'href_change':
      self._previous_action.WillRunAction(tab)
      old_url = tab.EvaluateJavaScript('document.location.href')
      self._previous_action.RunAction(tab)
      tab.WaitForJavaScriptExpression(
          'document.location.href != "%s"' % old_url, self.timeout)
