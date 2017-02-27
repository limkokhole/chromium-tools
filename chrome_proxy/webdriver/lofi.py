# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import common
from common import TestDriver
from common import IntegrationTest


class LoFi(IntegrationTest):

  #  Checks that the compressed image is below a certain threshold.
  #  The test page is uncacheable otherwise a cached page may be served that
  #  doesn't have the correct via headers.
  def testLoFi(self):
    with TestDriver() as test_driver:
      test_driver.AddChromeArg('--enable-spdy-proxy-auth')
      test_driver.AddChromeArg('--data-reduction-proxy-lo-fi=always-on')
      # Disable server experiments such as tamper detection.
      test_driver.AddChromeArg('--data-reduction-proxy-server-experiments-'
                               'disabled')
      test_driver.AddChromeArg('--disable-quic')

      test_driver.LoadURL('http://check.googlezip.net/static/index.html')

      lofi_responses = 0
      for response in test_driver.GetHTTPResponses():
        if not response.url.endswith('png'):
          continue
        if not response.request_headers:
          continue
        self.assertHasChromeProxyViaHeader(response)
        content_length = response.response_headers['content-length']
        cpat_request = response.request_headers['chrome-proxy-accept-transform']
        cpct_response = response.response_headers[
                          'chrome-proxy-content-transform']
        if ('empty-image' in cpct_response):
          lofi_responses = lofi_responses + 1
          self.assertIn('empty-image', cpat_request)
          self.assertTrue(int(content_length) < 100)

      # Verify that Lo-Fi responses were seen.
      self.assertNotEqual(0, lofi_responses)

  # Checks that a Lite Page is served and that the ignore_preview_blacklist
  # experiment is being used.
  def testLitePage(self):
    with TestDriver() as test_driver:
      test_driver.AddChromeArg('--enable-spdy-proxy-auth')
      test_driver.AddChromeArg('--data-reduction-proxy-lo-fi=always-on')
      test_driver.AddChromeArg('--enable-data-reduction-proxy-lite-page')

      test_driver.LoadURL('http://check.googlezip.net/test.html')

      lite_page_responses = 0
      for response in test_driver.GetHTTPResponses():
        # Skip CSI requests when validating Lite Page headers. CSI requests
        # aren't expected to have LoFi headers.
        if '/csi?' in response.url:
          continue
        if response.url.startswith('data:'):
          continue
        chrome_proxy_request = response.request_headers['chrome-proxy']
        cpat_request = response.request_headers['chrome-proxy-accept-transform']
        cpct_response = response.response_headers[
                          'chrome-proxy-content-transform']
        self.assertHasChromeProxyViaHeader(response)
        self.assertIn('exp=ignore_preview_blacklist',
          chrome_proxy_request)
        if ('lite-page' in cpct_response):
          lite_page_responses = lite_page_responses + 1
          self.assertIn('lite-page', cpat_request)

      # Verify that a Lite Page response for the main frame was seen.
      self.assertEqual(1, lite_page_responses)

if __name__ == '__main__':
  IntegrationTest.RunAllTests()
