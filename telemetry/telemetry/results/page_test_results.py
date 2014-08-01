# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import collections
import copy
import logging
import traceback

from telemetry import value as value_module
from telemetry.results import page_run
from telemetry.value import failure
from telemetry.value import skip


class PageTestResults(object):
  def __init__(self, output_stream=None, output_formatters=None, trace_tag=''):
    """
    Args:
      output_stream: The output stream to use to write test results.
      output_formatters: A list of output formatters. The output
          formatters are typically used to format the test results, such
          as CsvOutputFormatter, which output the test results as CSV.
      trace_tag: A string to append to the buildbot trace
      name. Currently only used for buildbot.
    """
    # TODO(chrishenry): Figure out if trace_tag is still necessary.

    super(PageTestResults, self).__init__()
    self._output_stream = output_stream
    self._output_formatters = (
        output_formatters if output_formatters is not None else [])
    self._trace_tag = trace_tag

    self._current_page_run = None
    self._all_page_runs = []
    self._representative_value_for_each_value_name = {}
    self._all_summary_values = []

  def __copy__(self):
    cls = self.__class__
    result = cls.__new__(cls)
    for k, v in self.__dict__.items():
      if isinstance(v, collections.Container):
        v = copy.copy(v)
      setattr(result, k, v)
    return result

  @property
  def all_page_specific_values(self):
    values = []
    for run in self._all_page_runs:
      values += run.values
    if self._current_page_run:
      values += self._current_page_run.values
    return values

  @property
  def all_summary_values(self):
    return self._all_summary_values

  @property
  def current_page(self):
    assert self._current_page_run, 'Not currently running test.'
    return self._current_page_run.page

  @property
  def all_page_runs(self):
    return self._all_page_runs

  @property
  def pages_that_succeeded(self):
    """Returns the set of pages that succeeded."""
    pages = set(run.page for run in self.all_page_runs)
    pages.difference_update(self.pages_that_failed)
    return pages

  @property
  def pages_that_failed(self):
    """Returns the set of failed pages."""
    failed_pages = set()
    for run in self.all_page_runs:
      if run.failed:
        failed_pages.add(run.page)
    return failed_pages

  @property
  def failures(self):
    values = self.all_page_specific_values
    return [v for v in values if isinstance(v, failure.FailureValue)]

  @property
  def skipped_values(self):
    values = self.all_page_specific_values
    return [v for v in values if isinstance(v, skip.SkipValue)]

  def _GetStringFromExcInfo(self, err):
    return ''.join(traceback.format_exception(*err))

  def WillRunPage(self, page):
    assert not self._current_page_run, 'Did not call DidRunPage.'
    self._current_page_run = page_run.PageRun(page)

  def DidRunPage(self, page, discard_run=False):  # pylint: disable=W0613
    """
    Args:
      page: The current page under test.
      discard_run: Whether to discard the entire run and all of its
          associated results.
    """
    assert self._current_page_run, 'Did not call WillRunPage.'
    if not discard_run:
      self._all_page_runs.append(self._current_page_run)
    self._current_page_run = None

  def WillAttemptPageRun(self):
    """To be called when a single attempt on a page run is starting.

    This is called between WillRunPage and DidRunPage and can be
    called multiple times, one for each attempt.
    """
    # Clear any values from previous attempts for this page run.
    self._current_page_run.ClearValues()

  def AddValue(self, value):
    assert self._current_page_run, 'Not currently running test.'
    self._ValidateValue(value)
    # TODO(eakuefner/chrishenry): Add only one skip per pagerun assert here
    self._current_page_run.AddValue(value)

  def AddSummaryValue(self, value):
    assert value.page is None
    self._ValidateValue(value)
    self._all_summary_values.append(value)

  def _ValidateValue(self, value):
    assert isinstance(value, value_module.Value)
    if value.name not in self._representative_value_for_each_value_name:
      self._representative_value_for_each_value_name[value.name] = value
    representative_value = self._representative_value_for_each_value_name[
        value.name]
    assert value.IsMergableWith(representative_value)

  # TODO(chrishenry): Kill this in a separate patch.
  def AddSuccess(self, page):
    pass

  def PrintSummary(self):
    for output_formatter in self._output_formatters:
      output_formatter.Format(self)

    if self.failures:
      logging.error('Failed pages:\n%s', '\n'.join(
          p.display_name for p in self.pages_that_failed))

    if self.skipped_values:
      logging.warning('Skipped pages:\n%s', '\n'.join(
          v.page.display_name for v in self.skipped_values))

  def FindPageSpecificValuesForPage(self, page, value_name):
    values = []
    for value in self.all_page_specific_values:
      if value.page == page and value.name == value_name:
        values.append(value)
    return values

  def FindAllPageSpecificValuesNamed(self, value_name):
    values = []
    for value in self.all_page_specific_values:
      if value.name == value_name:
        values.append(value)
    return values
