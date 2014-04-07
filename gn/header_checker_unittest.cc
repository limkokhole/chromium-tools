// Copyright 2014 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include <vector>

#include "testing/gtest/include/gtest/gtest.h"
#include "tools/gn/header_checker.h"
#include "tools/gn/scheduler.h"
#include "tools/gn/target.h"
#include "tools/gn/test_with_scope.h"

namespace {

class HeaderCheckerTest : public testing::Test {
 public:
  HeaderCheckerTest()
      : a_(setup_.settings(), Label(SourceDir("//"), "a")),
        b_(setup_.settings(), Label(SourceDir("//"), "a")),
        c_(setup_.settings(), Label(SourceDir("//"), "c")) {
    a_.deps().push_back(LabelTargetPair(&b_));
    b_.deps().push_back(LabelTargetPair(&c_));

    targets_.push_back(&a_);
    targets_.push_back(&b_);
    targets_.push_back(&c_);
  }

 protected:
  Scheduler scheduler_;

  TestWithScope setup_;

  // Some headers that are automatically set up with a dependency chain.
  // a -> b -> c
  Target a_;
  Target b_;
  Target c_;

  std::vector<const Target*> targets_;
};

}  // namespace

TEST_F(HeaderCheckerTest, IsDependencyOf) {
  scoped_refptr<HeaderChecker> checker(
      new HeaderChecker(setup_.build_settings(), targets_));

  EXPECT_FALSE(checker->IsDependencyOf(&a_, &a_));
  EXPECT_TRUE(checker->IsDependencyOf(&b_, &a_));
  EXPECT_TRUE(checker->IsDependencyOf(&c_, &a_));
  EXPECT_FALSE(checker->IsDependencyOf(&a_, &c_));
}

TEST_F(HeaderCheckerTest, CheckInclude) {
  // Add a disconnected target d with a header to check that you have to have
  // to depend on a target listing a header.
  Target d(setup_.settings(), Label(SourceDir("//"), "d"));
  SourceFile d_header("//d_header.h");
  d.sources().push_back(SourceFile(d_header));

  // Add a header on B and say everything in B is public.
  SourceFile b_public("//b_public.h");
  b_.sources().push_back(b_public);
  c_.set_all_headers_public(true);

  // Add a public and private header on C.
  SourceFile c_public("//c_public.h");
  SourceFile c_private("//c_private.h");
  c_.sources().push_back(c_private);
  c_.public_headers().push_back(c_public);
  c_.set_all_headers_public(false);

  targets_.push_back(&d);
  scoped_refptr<HeaderChecker> checker(
      new HeaderChecker(setup_.build_settings(), targets_));

  // A file in target A can't include a header from D because A has no
  // dependency on D.
  Err err;
  SourceFile source_file("//some_file.cc");
  EXPECT_FALSE(checker->CheckInclude(&a_, source_file, d_header, &err));
  EXPECT_TRUE(err.has_error());

  // A can include the public header in B.
  err = Err();
  EXPECT_TRUE(checker->CheckInclude(&a_, source_file, b_public, &err));
  EXPECT_FALSE(err.has_error());

  // Check A depending on the public and private headers in C.
  err = Err();
  EXPECT_TRUE(checker->CheckInclude(&a_, source_file, c_public, &err));
  EXPECT_FALSE(err.has_error());
  EXPECT_FALSE(checker->CheckInclude(&a_, source_file, c_private, &err));
  EXPECT_TRUE(err.has_error());

  // A can depend on a random file unknown to the build.
  err = Err();
  EXPECT_TRUE(checker->CheckInclude(&a_, source_file, SourceFile("//random.h"),
                                    &err));
  EXPECT_FALSE(err.has_error());
}
