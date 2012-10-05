# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import logging
import os
import unittest

from chrome_remote_control import browser_options
from chrome_remote_control import browser_finder
from chrome_remote_control import google_credentials_backend
from chrome_remote_control import simple_mock

_ = simple_mock.DONT_CARE

class MockTab(simple_mock.MockObject):
  def __init__(self):
    super(MockTab, self).__init__()
    self.runtime = simple_mock.MockObject(self)
    self.page = simple_mock.MockObject(self)

class TestGoogleCredentialsBackend(unittest.TestCase):
  def testRealLoginIfPossible(self):
    credentials_path = os.path.join(
      os.path.dirname(__file__),
      '..', 'unittest_data', 'internal', 'google_test_credentials.json')
    if not os.path.exists(credentials_path):
      logging.warning(
        ('%s credentials file not found. Will not be able to fully'
         'verify google_credentials_backend.') %
        credentials_path)
      return

    options = browser_options.options_for_unittests.Copy()
    with browser_finder.FindBrowser(options).Create() as b:
      b.credentials.credentials_path = credentials_path
      with b.ConnectToNthTab(0) as tab:
        ret = b.credentials.LoginNeeded(tab, 'google')
        self.assertTrue(ret)

  def testLoginUsingMock(self): # pylint: disable=R0201
    tab = MockTab()

    backend = google_credentials_backend.GoogleCredentialsBackend()
    config = {'username': 'blah',
              'password': 'blargh'}

    tab.page.ExpectCall('Navigate', 'https://accounts.google.com/')
    tab.runtime.ExpectCall('Evaluate', _).WillReturn(False)
    tab.runtime.ExpectCall('Evaluate', _).WillReturn(True)
    tab.ExpectCall('WaitForDocumentReadyStateToBeInteractiveOrBetter')

    def VerifyEmail(js):
      assert 'Email' in js
      assert 'blah' in js
    tab.runtime.ExpectCall('Execute', _).WhenCalled(VerifyEmail)

    def VerifyPw(js):
      assert 'Passwd' in js
      assert 'largh' in js
    tab.runtime.ExpectCall('Execute', _).WhenCalled(VerifyPw)

    def VerifySubmit(js):
      assert '.submit' in js
    tab.runtime.ExpectCall('Execute', _).WhenCalled(VerifySubmit)

    # Checking for form still up.
    tab.runtime.ExpectCall('Evaluate', _).WillReturn(False)

    backend.LoginNeeded(tab, config)

