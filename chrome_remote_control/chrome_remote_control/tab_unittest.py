# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import browser
import browser_finder
import browser_options
import tab
import unittest

class TabTest(unittest.TestCase):
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

  def testLoadUrlAndWaitToForCompleteState(self):
    self._tab.BeginToLoadUrl("http://www.google.com")
    self._tab.WaitForDocumentReadyStateToBeComplete()

  def testLoadUrlAndWaitToForInteractiveState(self):
    self._tab.BeginToLoadUrl("http://www.google.com")
    self._tab.WaitForDocumentReadyStateToBeInteractiveOrBetter()
