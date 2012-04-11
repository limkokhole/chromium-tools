#!/usr/bin/env python
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import cStringIO
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
VERBOSE = False
FILENAME = os.path.basename(__file__)


class CalledProcessError(subprocess.CalledProcessError):
  """Makes 2.6 version act like 2.7"""
  def __init__(self, returncode, cmd, output, cwd):
    super(CalledProcessError, self).__init__(returncode, cmd)
    self.output = output
    self.cwd = cwd

  def __str__(self):
    return super(CalledProcessError, self).__str__() + (
        '\n'
        'cwd=%s\n%s') % (self.cwd, self.output)


class Isolate(unittest.TestCase):
  def setUp(self):
    # The reason is that FILENAME --ok is run in a temporary directory
    # without access to isolate.py
    import isolate
    self.isolate = isolate
    self.tempdir = tempfile.mkdtemp()
    self.result = os.path.join(self.tempdir, 'result')
    if VERBOSE:
      print

  def tearDown(self):
    shutil.rmtree(self.tempdir)

  def _expected_tree(self, files):
    self.assertEquals(sorted(files), sorted(os.listdir(self.tempdir)))

  def _expected_result(self, with_hash, files, args, read_only):
    if sys.platform == 'win32':
      mode = lambda _: 420
    else:
      # 4 modes are supported, 0755 (rwx), 0644 (rw), 0555 (rx), 0444 (r)
      min_mode = 0444
      if not read_only:
        min_mode |= 0200
      def mode(filename):
        return (min_mode | 0111) if filename.endswith('.py') else min_mode
    expected = {
      u'command':
        [unicode(sys.executable)] +
          [unicode(x) for x in args],
      u'files': dict((unicode(f), {u'mode': mode(f)}) for f in files),
      u'relative_cwd': u'.',
      u'read_only': False,
    }
    if with_hash:
      for filename in expected[u'files']:
        # Calculate our hash.
        h = hashlib.sha1()
        h.update(open(os.path.join(ROOT_DIR, filename), 'rb').read())
        expected[u'files'][filename][u'sha-1'] = h.hexdigest()

    actual = json.load(open(self.result, 'rb'))
    self.assertEquals(expected, actual)
    return expected

  def _execute(self, args, need_output=False):
    cmd = [
      sys.executable, os.path.join(ROOT_DIR, 'isolate.py'),
      '--root', ROOT_DIR,
      '--result', self.result,
    ]
    if need_output or not VERBOSE:
      stdout = subprocess.PIPE
      stderr = subprocess.STDOUT
    else:
      cmd.extend(['-v'] * 3)
      stdout = None
      stderr = None
    cwd = ROOT_DIR
    p = subprocess.Popen(
        cmd + args,
        stdout=stdout,
        stderr=stderr,
        cwd=cwd,
        universal_newlines=True)
    out = p.communicate()[0]
    if p.returncode:
      raise CalledProcessError(p.returncode, cmd, out, cwd)
    return out

  def test_help_modes(self):
    # Check coherency in the help and implemented modes.
    p = subprocess.Popen(
        [sys.executable, os.path.join(ROOT_DIR, 'isolate.py'), '--help'],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=ROOT_DIR)
    out = p.communicate()[0].splitlines()
    self.assertEquals(0, p.returncode)
    out = out[out.index('') + 1:]
    out = out[:out.index('')]
    modes = [re.match(r'^  (\w+) .+', l) for l in out]
    modes = tuple(m.group(1) for m in modes if m)
    # Keep the list hard coded.
    expected = ('check', 'hashtable', 'remap', 'run', 'trace')
    self.assertEquals(expected, modes)
    self.assertEquals(expected, modes)
    for mode in modes:
      self.assertTrue(hasattr(self, 'test_%s' % mode), mode)
    self._expected_tree([])

  def test_check(self):
    cmd = [
      '--mode', 'check',
      FILENAME,
    ]
    self._execute(cmd)
    self._expected_tree(['result'])
    self._expected_result(
        False,
        [FILENAME],
        [os.path.join('.', FILENAME)],
        False)

  def test_check_non_existant(self):
    cmd = [
      '--mode', 'check',
      'NonExistentFile',
    ]
    try:
      self._execute(cmd)
      self.fail()
    except subprocess.CalledProcessError:
      pass
    self._expected_tree([])

  def test_check_directory_no_slash(self):
    cmd = [
        '--mode', 'check',
        # Trailing slash missing.
        os.path.join('data', 'isolate'),
    ]
    try:
      self._execute(cmd)
      self.fail()
    except subprocess.CalledProcessError:
      pass
    self._expected_tree([])

  def test_check_abs_path(self):
    cmd = [
      '--mode', 'check',
      FILENAME,
      '--',
      os.path.join(ROOT_DIR, FILENAME),
    ]
    self._execute(cmd)
    self._expected_tree(['result'])
    self._expected_result(
        False, [FILENAME], [FILENAME], False)

  def test_hashtable(self):
    cmd = [
      '--mode', 'hashtable',
      '--outdir', self.tempdir,
      FILENAME,
      os.path.join('data', 'isolate') + os.path.sep,
    ]
    self._execute(cmd)
    files = [
      FILENAME,
      os.path.join('data', 'isolate', 'test_file1.txt'),
      os.path.join('data', 'isolate', 'test_file2.txt'),
    ]
    data = self._expected_result(
        True, files, [os.path.join('.', FILENAME)], False)
    self._expected_tree(
        [f['sha-1'] for f in data['files'].itervalues()] + ['result'])

  def test_remap(self):
    cmd = [
      '--mode', 'remap',
      '--outdir', self.tempdir,
      FILENAME,
    ]
    self._execute(cmd)
    self._expected_tree([FILENAME, 'result'])
    self._expected_result(
        False,
        [FILENAME],
        [os.path.join('.', FILENAME)],
        False)

  def test_run(self):
    cmd = [
      '--mode', 'run',
      FILENAME,
      '--',
      sys.executable, FILENAME, '--ok',
    ]
    self._execute(cmd)
    self._expected_tree(['result'])
    # cmd[0] is not generated from infiles[0] so it's not using a relative path.
    self._expected_result(
        False, [FILENAME], [FILENAME, '--ok'], False)

  def test_run_fail(self):
    cmd = [
      '--mode', 'run',
      FILENAME,
      '--',
      sys.executable, FILENAME, '--fail',
    ]
    try:
      self._execute(cmd)
      self.fail()
    except subprocess.CalledProcessError:
      pass
    self._expected_tree([])

  def test_trace(self):
    cmd = [
      '--mode', 'trace',
      FILENAME,
      '--',
      sys.executable, os.path.join(ROOT_DIR, FILENAME), '--ok',
    ]
    out = self._execute(cmd, True)
    expected_tree = ['result', 'result.log']
    if sys.platform == 'win32':
      expected_tree.append('result.log.etl')
    self._expected_tree(expected_tree)
    # The 'result.log' log is OS-specific so we can't read it but we can read
    # the gyp result.
    # cmd[0] is not generated from infiles[0] so it's not using a relative path.
    self._expected_result(
        False, [FILENAME], [FILENAME, '--ok'], False)

    expected_value = {
      'conditions': [
        ['OS=="%s"' % self.isolate.trace_inputs.get_flavor(), {
          'variables': {
            'isolate_files': [
              '<(DEPTH)/%s' % FILENAME,
            ],
          },
        }],
      ],
    }
    expected_buffer = cStringIO.StringIO()
    self.isolate.trace_inputs.pretty_print(expected_value, expected_buffer)
    self.assertEquals(expected_buffer.getvalue(), out)


def main():
  global VERBOSE
  VERBOSE = '-v' in sys.argv
  level = logging.DEBUG if VERBOSE else logging.ERROR
  logging.basicConfig(level=level)
  if len(sys.argv) == 1:
    unittest.main()
  if sys.argv[1] == '--ok':
    return 0
  if sys.argv[1] == '--fail':
    return 1

  unittest.main()


if __name__ == '__main__':
  sys.exit(main())
