# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import time

from telemetry.results import progress_reporter
from telemetry.value import failure
from telemetry.value import skip


class GTestProgressReporter(progress_reporter.ProgressReporter):
  """A progress reporter that outputs the progress report in gtest style."""

  def __init__(self, output_stream, output_skipped_tests_summary=False):
    super(GTestProgressReporter, self).__init__(output_stream)
    self._timestamp = None
    self._output_skipped_tests_summary = output_skipped_tests_summary

  def _GetMs(self):
    assert self._timestamp is not None, 'Did not call WillRunPage.'
    return (time.time() - self._timestamp) * 1000

  def DidAddValue(self, value):
    super(GTestProgressReporter, self).DidAddValue(value)
    if isinstance(value, failure.FailureValue):
      print >> self.output_stream, failure.GetStringFromExcInfo(
          value.exc_info)
      self.output_stream.flush()
    elif isinstance(value, skip.SkipValue):
      print >> self.output_stream, '===== SKIPPING TEST %s: %s =====' % (
          value.page.display_name, value.reason)
    # TODO(chrishenry): Consider outputting metric values as well. For
    # e.g., it can replace BuildbotOutputFormatter in
    # --output-format=html, which we used only so that users can grep
    # the results without opening results.html.

  def WillRunPage(self, page_test_results):
    super(GTestProgressReporter, self).WillRunPage(page_test_results)
    print >> self.output_stream, '[ RUN      ]', (
        page_test_results.current_page.display_name)
    self.output_stream.flush()
    self._timestamp = time.time()

  def DidRunPage(self, page_test_results):
    super(GTestProgressReporter, self).DidRunPage(page_test_results)
    page = page_test_results.current_page
    if page_test_results.current_page_run.failed:
      print >> self.output_stream, '[  FAILED  ]', page.display_name, (
          '(%0.f ms)' % self._GetMs())
    else:
      print >> self.output_stream, '[       OK ]', page.display_name, (
          '(%0.f ms)' % self._GetMs())
    self.output_stream.flush()

  def WillAttemptPageRun(self, page_test_results, attempt_count, max_attempts):
    super(GTestProgressReporter, self).WillAttemptPageRun(
        page_test_results, attempt_count, max_attempts)
    # A failed attempt will have at least 1 value.
    if attempt_count != 1:
      print >> self.output_stream, (
          '===== RETRYING PAGE RUN (attempt %s out of %s allowed) =====' % (
              attempt_count, max_attempts))
      print >> self.output_stream, (
          'Page run attempt failed and will be retried. '
          'Discarding previous results.')

  def DidFinishAllTests(self, page_test_results):
    super(GTestProgressReporter, self).DidFinishAllTests(page_test_results)
    successful_runs = []
    failed_runs = []
    for run in page_test_results.all_page_runs:
      if run.failed:
        failed_runs.append(run)
      else:
        successful_runs.append(run)

    unit = 'test' if len(successful_runs) == 1 else 'tests'
    print >> self.output_stream, '[  PASSED  ]', (
        '%d %s.' % (len(successful_runs), unit))
    if len(failed_runs) > 0:
      unit = 'test' if len(failed_runs) == 1 else 'tests'
      print >> self.output_stream, '[  FAILED  ]', (
          '%d %s, listed below:' % (len(page_test_results.failures), unit))
      for failed_run in failed_runs:
        print >> self.output_stream, '[  FAILED  ] ', (
            failed_run.page.display_name)
      print >> self.output_stream
      count = len(failed_runs)
      unit = 'TEST' if count == 1 else 'TESTS'
      print >> self.output_stream, '%d FAILED %s' % (count, unit)
    print >> self.output_stream

    if self._output_skipped_tests_summary:
      if len(page_test_results.skipped_values) > 0:
        print >> self.output_stream, 'Skipped pages:\n%s\n' % ('\n'.join(
            v.page.display_name for v in page_test_results.skipped_values))

    self.output_stream.flush()
