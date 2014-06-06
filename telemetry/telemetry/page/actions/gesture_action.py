# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from telemetry.page.actions import page_action
from telemetry.page.actions import wait
from telemetry import decorators
from telemetry.page.actions import action_runner

class GestureAction(page_action.PageAction):
  def __init__(self, attributes=None):
    super(GestureAction, self).__init__(attributes)
    if not hasattr(self, 'automatically_record_interaction'):
      self.automatically_record_interaction = True

    if hasattr(self, 'wait_after'):
      self.wait_action = wait.WaitAction(self.wait_after)
    else:
      self.wait_action = None

    assert self.wait_until is None or self.wait_action is None, (
      'gesture cannot have wait_after and wait_until at the same time.')

  def RunAction(self, tab):
    runner = action_runner.ActionRunner(tab)
    if self.wait_action:
      interaction_name = 'Action_%s' % self.__class__.__name__
    else:
      interaction_name = 'Gesture_%s' % self.__class__.__name__

    interaction = None
    if self.automatically_record_interaction:
      interaction = runner.BeginInteraction(interaction_name, is_smooth=True)

    self.RunGesture(tab)
    if self.wait_action:
      self.wait_action.RunAction(tab)

    if interaction is not None:
      interaction.End()

  def RunGesture(self, tab):
    raise NotImplementedError()

  @staticmethod
  def GetGestureSourceTypeFromOptions(tab):
    gesture_source_type = tab.browser.synthetic_gesture_source_type
    return 'chrome.gpuBenchmarking.' + gesture_source_type.upper() + '_INPUT'

  @staticmethod
  @decorators.Cache
  def IsGestureSourceTypeSupported(tab, gesture_source_type):
    # TODO(dominikg): remove once support for
    #                 'chrome.gpuBenchmarking.gestureSourceTypeSupported' has
    #                 been rolled into reference build.
    if tab.EvaluateJavaScript("""
        typeof chrome.gpuBenchmarking.gestureSourceTypeSupported ===
            'undefined'"""):
      return (tab.browser.platform.GetOSName() != 'mac' or
              gesture_source_type.lower() != 'touch')

    return tab.EvaluateJavaScript("""
        chrome.gpuBenchmarking.gestureSourceTypeSupported(
            chrome.gpuBenchmarking.%s_INPUT)"""
        % (gesture_source_type.upper()))
