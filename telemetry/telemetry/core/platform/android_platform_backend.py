# Copyright (c) 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import logging
import os
import subprocess
import tempfile

from telemetry.core import exceptions
from telemetry.core import platform
from telemetry.core import util
from telemetry.core.platform import proc_supporting_platform_backend

# Get build/android scripts into our path.
util.AddDirToPythonPath(util.GetChromiumSrcDir(), 'build', 'android')
from pylib.perf import cache_control  # pylint: disable=F0401
from pylib.perf import perf_control  # pylint: disable=F0401
from pylib.perf import thermal_throttle  # pylint: disable=F0401

try:
  from pylib.perf import surface_stats_collector  # pylint: disable=F0401
except Exception:
  surface_stats_collector = None


_HOST_APPLICATIONS = [
    'ipfw',
    ]


class AndroidPlatformBackend(
    proc_supporting_platform_backend.ProcSupportingPlatformBackend):
  def __init__(self, adb, no_performance_mode):
    super(AndroidPlatformBackend, self).__init__()
    self._adb = adb
    self._surface_stats_collector = None
    self._perf_tests_setup = perf_control.PerfControl(self._adb)
    self._thermal_throttle = thermal_throttle.ThermalThrottle(self._adb)
    self._no_performance_mode = no_performance_mode
    self._raw_display_frame_rate_measurements = []
    self._host_platform_backend = platform.CreatePlatformBackendForCurrentOS()
    self._can_access_protected_file_contents = \
        self._adb.CanAccessProtectedFileContents()
    self._video_recorder = None
    self._video_output = None
    if self._no_performance_mode:
      logging.warning('CPU governor will not be set!')

  def IsRawDisplayFrameRateSupported(self):
    return True

  def StartRawDisplayFrameRateMeasurement(self):
    assert not self._surface_stats_collector
    # Clear any leftover data from previous timed out tests
    self._raw_display_frame_rate_measurements = []
    self._surface_stats_collector = \
        surface_stats_collector.SurfaceStatsCollector(self._adb)
    self._surface_stats_collector.Start()

  def StopRawDisplayFrameRateMeasurement(self):
    self._surface_stats_collector.Stop()
    for r in self._surface_stats_collector.GetResults():
      self._raw_display_frame_rate_measurements.append(
          platform.Platform.RawDisplayFrameRateMeasurement(
              r.name, r.value, r.unit))

    self._surface_stats_collector = None

  def GetRawDisplayFrameRateMeasurements(self):
    ret = self._raw_display_frame_rate_measurements
    self._raw_display_frame_rate_measurements = []
    return ret

  def SetFullPerformanceModeEnabled(self, enabled):
    if self._no_performance_mode:
      return
    if enabled:
      self._perf_tests_setup.SetHighPerfMode()
    else:
      self._perf_tests_setup.SetDefaultPerfMode()

  def CanMonitorThermalThrottling(self):
    return True

  def IsThermallyThrottled(self):
    return self._thermal_throttle.IsThrottled()

  def HasBeenThermallyThrottled(self):
    return self._thermal_throttle.HasBeenThrottled()

  def GetSystemCommitCharge(self):
    for line in self._adb.RunShellCommand('dumpsys meminfo', log_result=False):
      if line.startswith('Total PSS: '):
        return int(line.split()[2]) * 1024
    return 0

  def GetCpuStats(self, pid):
    if not self._can_access_protected_file_contents:
      logging.warning('CPU stats cannot be retrieved on non-rooted device.')
      return {}
    return super(AndroidPlatformBackend, self).GetCpuStats(pid)

  def GetCpuTimestamp(self):
    if not self._can_access_protected_file_contents:
      logging.warning('CPU timestamp cannot be retrieved on non-rooted device.')
      return {}
    return super(AndroidPlatformBackend, self).GetCpuTimestamp()

  def GetMemoryStats(self, pid):
    self._adb.PurgeUnpinnedAshmem()
    memory_usage = self._adb.GetMemoryUsageForPid(pid)[0]
    return {'ProportionalSetSize': memory_usage['Pss'] * 1024,
            'SharedDirty': memory_usage['Shared_Dirty'] * 1024,
            'PrivateDirty': memory_usage['Private_Dirty'] * 1024,
            'VMPeak': memory_usage['VmHWM'] * 1024}

  def GetIOStats(self, pid):
    return {}

  def GetChildPids(self, pid):
    child_pids = []
    ps = self._GetPsOutput(['pid', 'name'])
    for curr_pid, curr_name in ps:
      if int(curr_pid) == pid:
        name = curr_name
        for curr_pid, curr_name in ps:
          if curr_name.startswith(name) and curr_name != name:
            child_pids.append(int(curr_pid))
        break
    return child_pids

  def GetCommandLine(self, pid):
    ps = self._GetPsOutput(['pid', 'name'])
    for curr_pid, curr_name in ps:
      if int(curr_pid) == pid:
        return curr_name
    raise exceptions.ProcessGoneException()

  def GetOSName(self):
    return 'android'

  def GetOSVersionName(self):
    return self._adb.GetBuildId()[0]

  def CanFlushIndividualFilesFromSystemCache(self):
    return False

  def FlushEntireSystemCache(self):
    cache = cache_control.CacheControl(self._adb)
    cache.DropRamCaches()

  def FlushSystemCacheForDirectory(self, directory, ignoring=None):
    raise NotImplementedError()

  def LaunchApplication(self, application, parameters=None):
    if application in _HOST_APPLICATIONS:
      self._host_platform_backend.LaunchApplication(application, parameters)
      return
    if not parameters:
      parameters = ''
    self._adb.RunShellCommand('am start ' + parameters + ' ' + application)

  def IsApplicationRunning(self, application):
    if application in _HOST_APPLICATIONS:
      return self._host_platform_backend.IsApplicationRunning(application)
    return len(self._adb.ExtractPid(application)) > 0

  def CanLaunchApplication(self, application):
    if application in _HOST_APPLICATIONS:
      return self._host_platform_backend.CanLaunchApplication(application)
    return True

  def InstallApplication(self, application):
    if application in _HOST_APPLICATIONS:
      self._host_platform_backend.InstallApplication(application)
      return
    raise NotImplementedError(
        'Please teach Telemetry how to install ' + application)

  def CanCaptureVideo(self):
    return self.GetOSVersionName() >= 'K'

  def StartVideoCapture(self, min_bitrate_mbps):
    assert not self._video_recorder, 'Already started video capture'
    min_bitrate_mbps = max(min_bitrate_mbps, 0.1)
    if min_bitrate_mbps > 100:
      raise ValueError('Android video capture cannot capture at %dmbps. '
                       'Max capture rate is 100mbps.' % min_bitrate_mbps)
    self._video_output = tempfile.mkstemp()[1]
    self._video_recorder = subprocess.Popen(
        [os.path.join(util.GetChromiumSrcDir(), 'build', 'android',
                      'screenshot.py'),
         '--video', '--bitrate', str(min_bitrate_mbps), '--file',
         self._video_output], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

  def StopVideoCapture(self):
    assert self._video_recorder, 'Must start video capture first'
    self._video_recorder.communicate(input='\n')
    self._video_recorder.wait()
    self._video_recorder = None

    # TODO(tonyg/szym): Decode the mp4 and yield the (time, bitmap) tuples.
    raise NotImplementedError("mp4 video saved to %s, but Telemetry doesn't "
                              "know how to decode it." % self._video_output)

  def _GetFileContents(self, fname):
    if not self._can_access_protected_file_contents:
      logging.warning('%s cannot be retrieved on non-rooted device.' % fname)
      return ''
    return '\n'.join(
        self._adb.GetProtectedFileContents(fname, log_result=False))

  def _GetPsOutput(self, columns, pid=None):
    assert columns == ['pid', 'name'] or columns == ['pid'], \
        'Only know how to return pid and name. Requested: ' + columns
    command = 'ps'
    if pid:
      command += ' -p %d' % pid
    ps = self._adb.RunShellCommand(command, log_result=False)[1:]
    output = []
    for line in ps:
      data = line.split()
      curr_pid = data[1]
      curr_name = data[-1]
      if columns == ['pid', 'name']:
        output.append([curr_pid, curr_name])
      else:
        output.append([curr_pid])
    return output
