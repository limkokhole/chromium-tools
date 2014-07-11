#!/usr/bin/env python
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""This script is used by chrome_tests.gypi's js2webui action to maintain the
argument lists and to generate inlinable tests.
"""

import json
import optparse
import os
import subprocess
import sys
import shutil


def main ():
  parser = optparse.OptionParser()
  parser.set_usage(
      "%prog v8_shell mock.js test_api.js js2webui.js "
      "testtype inputfile inputrelfile cxxoutfile jsoutfile")
  parser.add_option('-v', '--verbose', action='store_true')
  parser.add_option('-n', '--impotent', action='store_true',
                    help="don't execute; just print (as if verbose)")
  parser.add_option('--deps_js', action="store",
                    help=("Path to deps.js for dependency resolution, " +
                          "optional."))
  (opts, args) = parser.parse_args()

  if len(args) != 9:
    parser.error('all arguments are required.')
  (v8_shell, mock_js, test_api, js2webui, test_type,
      inputfile, inputrelfile, cxxoutfile, jsoutfile) = args
  cmd = [v8_shell]
  icudatafile = os.path.join(os.path.dirname(v8_shell), 'icudtl.dat')
  if os.path.exists(icudatafile):
    cmd.extend(['--icu-data-file=%s' % icudatafile])
  arguments = [js2webui, inputfile, inputrelfile, opts.deps_js,
               cxxoutfile, test_type]
  cmd.extend(['-e', "arguments=" + json.dumps(arguments), mock_js,
         test_api, js2webui])
  if opts.verbose or opts.impotent:
    print cmd
  if not opts.impotent:
    try:
      p = subprocess.Popen(
          cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
      out, err = p.communicate()
      if p.returncode:
        # TODO(jochen): Remove once crbug.com/370551 is resolved.
        if sys.platform == 'darwin':
          sys.path.insert(0, '/Developer/Library/PrivateFrameworks/'
                             'LLDB.framework/Resources/Python')
          try:
            import lldb
          except:
            raise Exception("Could not load lldb module")
          debugger = lldb.SBDebugger.Create()
          debugger.SetAsync(False)
          target = debugger.CreateTargetWithFileAndArch(
              cmd[0], lldb.LLDB_ARCH_DEFAULT)
          if not target:
            raise Exception("Failed to create d8 target")
          process = target.LaunchSimple(cmd[1:], None, os.getcwd())
          if not process:
            raise Exception("Failed to start d8")
          if process.GetState() == lldb.eStateStopped:
            for thread in process:
              print "Thread (id %d)" % thread.GetThreadID()
              for frame in thread:
                print frame
              print ""
            raise Exception(
                "d8 crashed, please report this at http://crbug.com/370551")
          else:
            # For some reason d8 worked this time...
            out = ''
            while True:
              s = process.GetSTDOUT(4096)
              if s == '':
                break
              out += s

      with open(cxxoutfile, 'wb') as f:
        f.write(out)
      shutil.copyfile(inputfile, jsoutfile)
    except Exception, ex:
      if os.path.exists(cxxoutfile):
        os.remove(cxxoutfile)
      if os.path.exists(jsoutfile):
        os.remove(jsoutfile)
      raise


if __name__ == '__main__':
 sys.exit(main())
