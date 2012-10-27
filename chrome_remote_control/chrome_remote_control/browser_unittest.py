# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import unittest

from chrome_remote_control import browser_finder
from chrome_remote_control import options_for_unittests

class BrowserTest(unittest.TestCase):
  def testBrowserCreation(self):
    options = options_for_unittests.Get()
    browser_to_create = browser_finder.FindBrowser(options)
    if not browser_to_create:
      raise Exception('No browser found, cannot continue test.')
    with browser_to_create.Create() as b:
      self.assertEquals(1, b.num_tabs)

      # Different browsers boot up to different things
      assert b.GetNthTabUrl(0)

  def testCommandLineOverriding(self):
    # This test starts the browser with --enable-benchmarking, which should
    # create a chrome.Interval namespace. This tests whether the command line is
    # being set.
    options = options_for_unittests.Get()

    flag1 = '--user-agent=chrome_remote_control'
    options.extra_browser_args.append(flag1)

    browser_to_create = browser_finder.FindBrowser(options)
    with browser_to_create.Create() as b:
      with b.ConnectToNthTab(0) as t:
        t.page.Navigate('http://www.google.com/')
        t.WaitForDocumentReadyStateToBeInteractiveOrBetter()
        self.assertEquals(t.runtime.Evaluate('navigator.userAgent'),
                          'chrome_remote_control')

  def testNewCloseTab(self):
    options = options_for_unittests.Get()
    browser_to_create = browser_finder.FindBrowser(options)
    with browser_to_create.Create() as b:
      self.assertEquals(1, b.num_tabs)
      existing_tab_url = b.GetNthTabUrl(0)
      b.NewTab()
      self.assertEquals(2, b.num_tabs)
      self.assertEquals(b.GetNthTabUrl(0), existing_tab_url)
      self.assertEquals(b.GetNthTabUrl(1), 'about:blank')
      b.CloseTab(1)
      self.assertEquals(1, b.num_tabs)
      self.assertEquals(b.GetNthTabUrl(0), existing_tab_url)
      self.assertRaises(AssertionError, b.CloseTab, 0)
