# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Checks C++ and Objective-C files for illegal includes."""

import codecs
import os
import re

from rules import Rule


class CppChecker(object):

  EXTENSIONS = [
      '.h',
      '.cc',
      '.m',
      '.mm',
  ]

  # The maximum number of non-include lines we can see before giving up.
  _MAX_UNINTERESTING_LINES = 50

  # The maximum line length, this is to be efficient in the case of very long
  # lines (which can't be #includes).
  _MAX_LINE_LENGTH = 128

  # This regular expression will be used to extract filenames from include
  # statements.
  _EXTRACT_INCLUDE_PATH = re.compile(
      '[ \t]*#[ \t]*(?:include|import)[ \t]+"(.*)"')

  def __init__(self, verbose):
    self._verbose = verbose

  def CheckLine(self, rules, line, fail_on_temp_allow=False):
    """Checks the given line with the given rule set.
    Returns a triplet (is_include, illegal_description, rule_type).

    If the line is an #include directive the first value will be True.
    If it is also an illegal include, the second value will be a
    string describing the error.  Otherwise, it will be None.  If
    fail_on_temp_allow is False, only Rule.DISALLOW rules will cause a
    problem to be reported.  If it is true, both Rule.DISALLOW and
    Rule.TEMP_ALLOW will cause an error.

    The last item in the triplet returns the type of rule that
    applied, one of Rule.ALLOW (which implies the second item is
    None), Rule.DISALLOW (which implies that the second item is not
    None) and Rule.TEMP_ALLOW (in which case the second item will be
    None only if fail_on_temp_allow is False).
    """
    found_item = self._EXTRACT_INCLUDE_PATH.match(line)
    if not found_item:
      return False, None, Rule.ALLOW  # Not a match

    include_path = found_item.group(1)

    if '\\' in include_path:
      return True, 'Include paths may not include backslashes', Rule.DISALLOW

    if '/' not in include_path:
      # Don't fail when no directory is specified. We may want to be more
      # strict about this in the future.
      if self._verbose:
        print ' WARNING: directory specified with no path: ' + include_path
      return True, None, Rule.ALLOW

    (allowed, why_failed) = rules.DirAllowed(include_path)
    if (allowed == Rule.DISALLOW or
        (fail_on_temp_allow and allowed == Rule.TEMP_ALLOW)):
      if self._verbose:
        retval = '\nFor %s' % rules
      else:
        retval = ''
      return True, retval + ('Illegal include: "%s"\n    Because of %s' %
          (include_path, why_failed)), allowed

    return True, None, allowed

  def CheckFile(self, rules, filepath):
    if self._verbose:
      print 'Checking: ' + filepath

    ret_val = ''  # We'll collect the error messages in here
    last_include = 0
    with codecs.open(filepath, encoding='utf-8') as f:
      in_if0 = 0
      for line_num, line in enumerate(f):
        if line_num - last_include > self._MAX_UNINTERESTING_LINES:
          break

        line = line.strip()

        # Check to see if we're at / inside a #if 0 block
        if line.startswith('#if 0'):
          in_if0 += 1
          continue
        if in_if0 > 0:
          if line.startswith('#if'):
            in_if0 += 1
          elif line.startswith('#endif'):
            in_if0 -= 1
          continue

        is_include, line_status, rule_type = self.CheckLine(rules, line)
        if is_include:
          last_include = line_num
        if line_status is not None:
          if len(line_status) > 0:  # Add newline to separate messages.
            line_status += '\n'
          ret_val += line_status

    return ret_val

  @staticmethod
  def IsCppFile(file_path):
    """Returns True iff the given path ends in one of the extensions
    handled by this checker.
    """
    return os.path.splitext(file_path)[1] in CppChecker.EXTENSIONS
