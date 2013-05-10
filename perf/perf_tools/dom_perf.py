# Copyright (c) 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json
import math

from telemetry.core import util
from telemetry.page import page_measurement


def _GeometricMean(values):
  """Compute a rounded geometric mean from an array of values."""
  if not values:
    return None
  # To avoid infinite value errors, make sure no value is less than 0.001.
  new_values = []
  for value in values:
    if value > 0.001:
      new_values.append(value)
    else:
      new_values.append(0.001)
  # Compute the sum of the log of the values.
  log_sum = sum(map(math.log, new_values))
  # Raise e to that sum over the number of values.
  mean = math.pow(math.e, (log_sum / len(new_values)))
  # Return the rounded mean.
  return int(round(mean))


SCORE_UNIT = 'score (bigger is better)'
SCORE_TRACE_NAME = 'score'


class DomPerf(page_measurement.PageMeasurement):
  @property
  def results_are_the_same_on_every_page(self):
    return False

  def WillNavigateToPage(self, page, tab):
    tab.EvaluateJavaScript('document.cookie = "__domperf_finished=0"')

  def MeasurePage(self, page, tab, results):
    def _IsDone():
      return tab.GetCookieByName('__domperf_finished') == '1'
    util.WaitFor(_IsDone, 600, poll_interval=5)

    data = json.loads(tab.EvaluateJavaScript('__domperf_result'))
    for suite in data['BenchmarkSuites']:
      # Skip benchmarks that we didn't actually run this time around.
      if len(suite['Benchmarks']) or suite['score']:
        results.Add(SCORE_TRACE_NAME, SCORE_UNIT,
                    suite['score'], suite['name'], 'unimportant')

  def DidRunPageSet(self, tab, results):
    # Now give the geometric mean as the total for the combined runs.
    scores = []
    for result in results.page_results:
      scores.append(result[SCORE_TRACE_NAME].output_value)
    total = _GeometricMean(scores)
    results.AddSummary(SCORE_TRACE_NAME, SCORE_UNIT, total, 'Total')
