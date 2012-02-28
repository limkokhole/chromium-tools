# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

{
  'targets': [
    {
      'target_name': 'json_schema_compiler_tests',
      'type': 'static_library',
      'variables': {
        'chromium_code': 1,
        'json_schema_files': [
          'arrays.json',
          'choices.json',
          'crossref.json',
          'enums.json',
          'objects.json',
          'simple_api.json',
        ],
        'cc_dir': 'tools/json_schema_compiler/test',
        'root_namespace': 'test::api',
      },
      'sources': [
        '<@(json_schema_files)',
      ],
      'includes': ['../../../build/json_schema_compile.gypi'],
    },
  ],
}
