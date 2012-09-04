# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import browser
import browser_finder
import browser_options
import tab_runtime
import unittest

class TabRuntimeTest(unittest.TestCase):
  def setUp(self):
    self._browser = None
    self._tab = None
    options = browser_options.options_for_unittests
    browser_to_create = browser_finder.FindBestPossibleBrowser(options)
    if not browser_to_create:
      raise Exception('No browser found, cannot continue test.')
    try:
      self._browser = browser_to_create.Create()
      self._tab = self._browser.ConnectToNthTab(0)
    except:
      self.tearDown()
      raise

  def tearDown(self):
    if self._tab:
      self._tab.Close()
    if self._browser:
      self._browser.Close()

  def testRuntimeEvaluateSimple(self):
    res = self._tab.runtime.Evaluate("1+1")
    assert res == 2

  def testRuntimeEvaluateThatFails(self):
    self.assertRaises(tab_runtime.EvaluateException,
                      lambda: self._tab.runtime.Evaluate("fsdfsdfsf"))

  def testRuntimeEvaluateOfSomethingThatCantJSONize(self):
    # TODO(nduca): This fails on Android. I wonder why?
    self.assertRaises(tab_runtime.EvaluateException,
                      lambda: self._tab.runtime.Evaluate("window"))
    pass

  def testRuntimeExecuteOfSomethingThatCantJSONize(self):
    self._tab.runtime.Execute("window");

  def testRuntimeLoadUrl(self):
    self._tab.BeginToLoadUrl("http://www.google.com")
