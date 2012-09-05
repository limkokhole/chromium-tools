# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import browser
import browser_finder
import browser_options
import tab_page
import unittest

class TabPageTest(unittest.TestCase):
  def setUp(self):
    self._browser = None
    self._tab = None
    options = browser_options.options_for_unittests
    browser_to_create = browser_finder.FindBrowser(options)
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

  def testPageNavigateToNormalUrl(self):
    res = self._tab.page.Navigate("http://www.google.com")
    self._tab.WaitForDocumentReadyStateToBeComplete()

  def testPageNavigateToUrlChanger(self):
    # The Url that we actually load is http://www.youtube.com/.
    res = self._tab.page.Navigate("http://youtube.com/")

    self._tab.WaitForDocumentReadyStateToBeComplete()

  def testPageNavigateToImpossibleURL(self):
    res = self._tab.page.Navigate("http://23f09f0f9fsdflajsfaldfkj2f3f.com")
    self._tab.WaitForDocumentReadyStateToBeComplete()
