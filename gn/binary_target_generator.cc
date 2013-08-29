// Copyright (c) 2013 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "tools/gn/binary_target_generator.h"

#include "tools/gn/config_values_generator.h"
#include "tools/gn/err.h"
#include "tools/gn/scope.h"
#include "tools/gn/variables.h"

BinaryTargetGenerator::BinaryTargetGenerator(Target* target,
                                             Scope* scope,
                                             const Token& function_token,
                                             Target::OutputType type,
                                             Err* err)
    : TargetGenerator(target, scope, function_token, err),
      output_type_(type) {
}

BinaryTargetGenerator::~BinaryTargetGenerator() {
}

void BinaryTargetGenerator::DoRun() {
  target_->set_output_type(output_type_);

  FillExternal();
  FillSources();
  FillConfigs();

  // Config values (compiler flags, etc.) set directly on this target.
  ConfigValuesGenerator gen(&target_->config_values(), scope_,
                            function_token_, scope_->GetSourceDir(), err_);
  gen.Run();
  if (err_->has_error())
    return;

  SetToolchainDependency();
}
