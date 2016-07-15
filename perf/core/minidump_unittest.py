# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from telemetry.testing import tab_test_case
from telemetry import decorators
from telemetry.core import util

class BrowserMinidumpTest(tab_test_case.TabTestCase):
  @decorators.Isolated
  @decorators.Enabled('mac')
  def testSymbolizeMinidump(self):
    self._browser.tabs.New().Navigate('chrome://gpucrash', timeout=5)
    crash_minidump_path = self._browser.GetMostRecentMinidumpPath()
    self.assertIsNotNone(crash_minidump_path)
    self.assertTrue(len(self._browser.GetAllUnsymbolizedMinidumpPaths()) == 1)

    # Now symbolize that minidump and make sure there are no longer any present
    self._browser.SymbolizeMinidump(crash_minidump_path)
    self.assertTrue(len(self._browser.GetAllUnsymbolizedMinidumpPaths()) == 0)

  @decorators.Isolated
  @decorators.Enabled('mac')
  def testMultipleCrashMinidumps(self):
    self._browser.tabs.New().Navigate('chrome://gpucrash', timeout=5)
    first_crash_path = self._browser.GetMostRecentMinidumpPath()
    self.assertIsNotNone(first_crash_path)
    all_paths = self._browser.GetAllMinidumpPaths()
    self.assertEquals(len(all_paths), 1)
    self.assertEqual(all_paths[0], first_crash_path)
    self.assertTrue(len(self._browser.GetAllUnsymbolizedMinidumpPaths()) == 1)

    # Restart the browser and then crash a second time
    self._RestartBrowser()

    # Start a new tab in the restarted browser
    new_tab = self._browser.tabs.New()
    new_tab.Navigate('http://www.google.com/',
        script_to_evaluate_on_commit='var foo = "bar";')
    # Wait until the javascript has run ensuring that
    # the new browser has restarted before we crash it again
    util.WaitFor(lambda: new_tab.EvaluateJavaScript('foo'), 60)

    self._browser.tabs.New().Navigate('chrome://gpucrash', timeout=5)
    second_crash_path = self._browser.GetMostRecentMinidumpPath()
    self.assertIsNotNone(second_crash_path)
    all_paths = self._browser.GetAllMinidumpPaths()
    self.assertEquals(len(all_paths), 2)
    # Check that both paths are now present and unsymbolized
    self.assertTrue(first_crash_path in all_paths)
    self.assertTrue(second_crash_path in all_paths)
    self.assertTrue(len(self._browser.GetAllUnsymbolizedMinidumpPaths()) == 2)

    # Now symbolize one of those paths and assert that there is still one
    # unsymbolized
    self._browser.SymbolizeMinidump(second_crash_path)
    self.assertEquals(len(self._browser.GetAllMinidumpPaths()), 2)
    self.assertEquals(self._browser.GetAllUnsymbolizedMinidumpPaths(),
      [first_crash_path])
