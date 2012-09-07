# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

{
  'targets': [
    {
      'target_name': 'forwarder2',
      'type': 'none',
      'dependencies': [
        'device_forwarder',
        'host_forwarder#host',
      ],
    },
    {
      'target_name': 'device_forwarder',
      'type': 'executable',
      'toolsets': ['target'],
      'dependencies': [
        '../../../base/base.gyp:base',
        '../common/common.gyp:android_tools_common',
      ],
      'include_dirs': [
        '../../..',
      ],
      'conditions': [
        # Warning: A PIE tool cannot run on ICS 4.0.4, so only
        #          build it as position-independent when ASAN
        #          is activated. See b/6587214 for details.
        [ 'asan==1', {
          'cflags': [
            '-fPIE',
          ],
          'ldflags': [
            '-pie',
          ],
        }],
      ],
      'sources': [
        'device_forwarder_main.cc',
        'socket.cc',
      ],
    },
    {
      'target_name': 'host_forwarder',
      'type': 'executable',
      'toolsets': ['host'],
      'dependencies': [
        '../../../base/base.gyp:base',
        '../common/common.gyp:android_tools_common',
      ],
      'include_dirs': [
        '../../..',
      ],
      'sources': [
        'host_forwarder_main.cc',
        'socket.cc',
      ],
    },
  ],
}
