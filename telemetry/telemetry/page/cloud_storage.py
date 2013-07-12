# Copyright (c) 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Wrappers for gsutil, for basic interaction with Google Cloud Storage."""

import cStringIO
import logging
import os
import subprocess
import sys
import tarfile
import urllib2

from telemetry.core import util


_GSUTIL_URL = 'http://storage.googleapis.com/pub/gsutil.tar.gz'
_DOWNLOAD_PATH = os.path.join(util.GetTelemetryDir(), 'third_party', 'gsutil')


def _DownloadGsutil():
  logging.info('Downloading gsutil')
  response = urllib2.urlopen(_GSUTIL_URL)
  with tarfile.open(fileobj=cStringIO.StringIO(response.read())) as tar_file:
    tar_file.extractall(os.path.dirname(_DOWNLOAD_PATH))
  logging.info('Downloaded gsutil to %s' % _DOWNLOAD_PATH)

  return os.path.join(_DOWNLOAD_PATH, 'gsutil')


def _FindGsutil():
  """Return the gsutil executable path. If we can't find it, download it."""
  search_paths = [_DOWNLOAD_PATH] + os.environ['PATH'].split(os.pathsep)

  # Look for a depot_tools installation.
  for path in search_paths:
    gsutil_path = os.path.join(path, 'third_party', 'gsutil', 'gsutil')
    if os.path.isfile(gsutil_path):
      return gsutil_path

  # Look for a gsutil installation.
  for path in search_paths:
    gsutil_path = os.path.join(path, 'gsutil')
    if os.path.isfile(gsutil_path):
      return gsutil_path

  # Failed to find it. Download it!
  return _DownloadGsutil()


def _RunCommand(args):
  gsutil_path = _FindGsutil()
  gsutil = subprocess.Popen([sys.executable, gsutil_path] + args,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  stdout, stderr = gsutil.communicate()

  if gsutil.returncode:
    raise Exception(stderr.splitlines()[-1])

  return stdout


def List(bucket):
  stdout = _RunCommand(['ls', 'gs://%s' % bucket])
  return [url.split('/')[-1] for url in stdout.splitlines()]


def Delete(bucket, remote_path):
  url = 'gs://%s/%s' % (bucket, remote_path)
  logging.debug('Deleting %s' % url)
  _RunCommand(['rm', url])


def Get(bucket, remote_path, local_path):
  url = 'gs://%s/%s' % (bucket, remote_path)
  logging.debug('Downloading %s to %s' % (url, local_path))
  _RunCommand(['cp', url, local_path])


def Insert(bucket, remote_path, local_path):
  url = 'gs://%s/%s' % (bucket, remote_path)
  logging.debug('Uploading %s to %s' % (local_path, url))
  _RunCommand(['cp', local_path, url])
