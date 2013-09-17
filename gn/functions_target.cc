// Copyright (c) 2013 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "tools/gn/functions.h"

#include "tools/gn/config_values_generator.h"
#include "tools/gn/err.h"
#include "tools/gn/parse_tree.h"
#include "tools/gn/scope.h"
#include "tools/gn/target_generator.h"
#include "tools/gn/value.h"

#define DEPENDENT_CONFIG_VARS \
    "  Dependent configs: all_dependent_configs, direct_dependent_configs\n"
#define DEPS_VARS \
    "  Deps: data, datadeps, deps, forward_dependent_configs_from, hard_dep\n"
#define GENERAL_TARGET_VARS \
    "  General: configs, external, source_prereqs, sources\n"

namespace functions {

namespace {

Value ExecuteGenericTarget(const char* target_type,
                           Scope* scope,
                           const FunctionCallNode* function,
                           const std::vector<Value>& args,
                           BlockNode* block,
                           Err* err) {
  if (!EnsureNotProcessingImport(function, scope, err) ||
      !EnsureNotProcessingBuildConfig(function, scope, err))
    return Value();
  Scope block_scope(scope);
  if (!FillTargetBlockScope(scope, function, target_type, block,
                            args, &block_scope, err))
    return Value();

  block->ExecuteBlockInScope(&block_scope, err);
  if (err->has_error())
    return Value();

  TargetGenerator::GenerateTarget(&block_scope, function->function(), args,
                                  target_type, err);

  block_scope.CheckForUnusedVars(err);
  return Value();
}

}  // namespace

// component -------------------------------------------------------------------

const char kComponent[] = "component";
const char kComponent_Help[] =
    "TODO(brettw) write this.";

Value RunComponent(Scope* scope,
                   const FunctionCallNode* function,
                   const std::vector<Value>& args,
                   BlockNode* block,
                   Err* err) {
  // A component is either a shared or static library, depending on the value
  // of |component_mode|.
  const Value* component_mode_value = scope->GetValue("component_mode");

  static const char helptext[] =
      "You're declaring a component here but have not defined "
      "\"component_mode\" to\neither \"shared_library\" or \"static_library\".";
  if (!component_mode_value) {
    *err = Err(function->function(), "No component mode set.", helptext);
    return Value();
  }
  if (component_mode_value->type() != Value::STRING ||
      (component_mode_value->string_value() != functions::kSharedLibrary &&
       component_mode_value->string_value() != functions::kStaticLibrary)) {
    *err = Err(function->function(), "Invalid component mode set.", helptext);
    return Value();
  }
  const std::string& component_mode = component_mode_value->string_value();

  if (!EnsureNotProcessingImport(function, scope, err))
    return Value();
  Scope block_scope(scope);
  if (!FillTargetBlockScope(scope, function, component_mode.c_str(), block,
                            args, &block_scope, err))
    return Value();

  block->ExecuteBlockInScope(&block_scope, err);
  if (err->has_error())
    return Value();

  TargetGenerator::GenerateTarget(&block_scope, function->function(), args,
                                  component_mode, err);
  return Value();
}

// copy ------------------------------------------------------------------------

const char kCopy[] = "copy";
const char kCopy_Help[] =
    "TODO(brettw) write this.";

Value RunCopy(const FunctionCallNode* function,
              const std::vector<Value>& args,
              Scope* scope,
              Err* err) {
  if (!EnsureNotProcessingImport(function, scope, err) ||
      !EnsureNotProcessingBuildConfig(function, scope, err))
    return Value();
  TargetGenerator::GenerateTarget(scope, function->function(), args,
                                  functions::kCopy, err);
  return Value();
}

// custom ----------------------------------------------------------------------

const char kCustom[] = "custom";
const char kCustom_Help[] =
    "custom: Declare a script-generated target.\n"
    "\n"
    "  This target type allows you to run a script over a set of source\n"
    "  files and generate a set of output files.\n"
    "\n"
    "  The script will be executed with the given arguments with the current\n"
    "  directory being that of the root build directory. If you pass\n"
    "  to your script, see \"gn help to_build_path\" for how to convert\n"
    "  file names to be relative to the build directory (file names in the\n"
    "  sources, outputs, and source_prereqs will be all treated as relative\n"
    "  to the current build file and converted as needed automatically).\n"
    "\n"
    "  There are two modes. The first mode is the \"per-file\" mode where you\n"
    "  specify a list of sources and the script is run once for each one as a\n"
    "  build rule. In this case, each file specified in the |outputs|\n"
    "  variable must be unique when applied to each source file (normally you\n"
    "  would reference |{{source_name_part}}| from within each one) or the\n"
    "  build system will get confused about how to build those files. You\n"
    "  should use the |source_prereqs| variable to list all additional\n"
    "  dependencies of your script: these will be added as dependencies for\n"
    "  each build step.\n"
    "\n"
    "  The second mode is when you just want to run a script once rather than\n"
    "  as a general rule over a set of files. In this case you don't list any\n"
    "  sources. Dependencies of your script are specified only in the\n"
    "  |source_prereqs| variable and your |outputs| variable should just list\n"
    "  all outputs.\n"
    "\n"
    "Variables:\n"
    "\n"
    "  args, deps, outputs, script*, source_prereqs, sources\n"
    "  * = required\n"
    "\n"
    "  There are some special substrings that will be searched for when\n"
    "  processing some variables:\n"
    "\n"
    "    {{source}}\n"
    "        Expanded in |args|, this is the name of the source file relative\n"
    "        to the build directory This is how you specify the current input\n"
    "        file to your script.\n"
    "\n"
    "    {{source_name_part}}\n"
    "        Expanded in |args| and |outputs|, this is just the filename part\n"
    "        of the current source file with no directory or extension. This\n"
    "        is how you specify a name transformation to the output. Normally\n"
    "        you would write an output as\n"
    "        \"$target_output_dir/{{source_name_part}}.o\".\n"
    "\n"
    "  All |outputs| files must be inside the output directory of the build.\n"
    "  You would generally use |$target_output_dir| or |$target_gen_dir| to\n"
    "  reference the output or generated intermediate file directories,\n"
    "  respectively.\n"
    "\n"
    "Examples:\n"
    "\n"
    "  # Runs the script over each IDL file. The IDL script will generate\n"
    "  # both a .cc and a .h file for each input.\n"
    "  custom(\"general_rule\") {\n"
    "    script = \"idl_processor.py\"\n"
    "    sources = [ \"foo.idl\", \"bar.idl\" ]\n"
    "    source_prereqs = [ \"my_configuration.txt\" ]\n"
    "    outputs = [ \"$target_gen_dir/{{source_name_part}}.h\",\n"
    "                \"$target_gen_dir/{{source_name_part}}.cc\" ]\n"
    "\n"
    "    # Note that since \"args\" is opaque to GN, if you specify paths\n"
    "    # here, you will need to convert it to be relative to the build\n"
    "    # directory using \"to_build_path()\".\n"
    "    args = [ \"{{source}}\",\n"
    "             \"-o\",\n"
    "             to_build_path(relative_target_gen_dir) + \"/\" +\n"
    "                 {{source_name_part}}.h\" ]\n"
    "  }\n"
    "\n"
    "  custom(\"just_run_this_guy_once\") {\n"
    "    script = \"doprocessing.py\"\n"
    "    source_prereqs = [ \"my_configuration.txt\" ]\n"
    "    outputs = [ \"$target_gen_dir/insightful_output.txt\" ]\n"
    "    args = [ \"--output_dir\", to_build_path(target_gen_dir) ]\n"
    "  }\n";

Value RunCustom(Scope* scope,
                const FunctionCallNode* function,
                const std::vector<Value>& args,
                BlockNode* block,
                Err* err) {
  return ExecuteGenericTarget(functions::kCustom, scope, function, args,
                              block, err);
}

// executable ------------------------------------------------------------------

const char kExecutable[] = "executable";
const char kExecutable_Help[] =
    "executable: Declare an executable target.\n"
    "\n"
    "Variables:\n"
    CONFIG_VALUES_VARS_HELP
    DEPS_VARS
    DEPENDENT_CONFIG_VARS
    GENERAL_TARGET_VARS;

Value RunExecutable(Scope* scope,
                    const FunctionCallNode* function,
                    const std::vector<Value>& args,
                    BlockNode* block,
                    Err* err) {
  return ExecuteGenericTarget(functions::kExecutable, scope, function, args,
                              block, err);
}

// group -----------------------------------------------------------------------

const char kGroup[] = "group";
const char kGroup_Help[] =
    "group: Declare a named group of targets.\n"
    "\n"
    "  This target type allows you to create meta-targets that just collect a\n"
    "  set of dependencies into one named target. Groups can additionally\n"
    "  specify configs that apply to their dependents.\n"
    "\n"
    "  Depending on a group is exactly like depending directly on that\n"
    "  group's deps. Direct dependent configs will get automatically fowarded\n"
    "  through the group so you shouldn't need to use\n"
    "  \"forward_dependent_configs_from.\n"
    "\n"
    "Variables:\n"
    DEPS_VARS
    DEPENDENT_CONFIG_VARS
    "  Other variables: external\n"
    "\n"
    "Example:\n"
    "  group(\"all\") {\n"
    "    deps = [\n"
    "      \"//project:runner\",\n"
    "      \"//project:unit_tests\",\n"
    "    ]\n"
    "  }\n";

Value RunGroup(Scope* scope,
               const FunctionCallNode* function,
               const std::vector<Value>& args,
               BlockNode* block,
               Err* err) {
  return ExecuteGenericTarget(functions::kGroup, scope, function, args,
                              block, err);
}

// shared_library --------------------------------------------------------------

const char kSharedLibrary[] = "shared_library";
const char kSharedLibrary_Help[] =
    "shared_library: Declare a shared library target.\n"
    "\n"
    "  A shared library will be specified on the linker line for targets\n"
    "  listing the shared library in its \"deps\". If you don't want this\n"
    "  (say you dynamically load the library at runtime), then you should\n"
    "  depend on the shared library via \"datadeps\" instead.\n"
    "\n"
    "Variables:\n"
    CONFIG_VALUES_VARS_HELP
    DEPS_VARS
    DEPENDENT_CONFIG_VARS
    GENERAL_TARGET_VARS;

Value RunSharedLibrary(Scope* scope,
                       const FunctionCallNode* function,
                       const std::vector<Value>& args,
                       BlockNode* block,
                       Err* err) {
  return ExecuteGenericTarget(functions::kSharedLibrary, scope, function, args,
                              block, err);
}

// static_library --------------------------------------------------------------

const char kStaticLibrary[] = "static_library";
const char kStaticLibrary_Help[] =
    "static_library: Declare a static library target.\n"
    "\n"
    "Variables:\n"
    CONFIG_VALUES_VARS_HELP
    DEPS_VARS
    DEPENDENT_CONFIG_VARS
    GENERAL_TARGET_VARS;

Value RunStaticLibrary(Scope* scope,
                       const FunctionCallNode* function,
                       const std::vector<Value>& args,
                       BlockNode* block,
                       Err* err) {
  return ExecuteGenericTarget(functions::kStaticLibrary, scope, function, args,
                              block, err);
}

// test ------------------------------------------------------------------------

const char kTest[] = "test";
const char kTest_Help[] =
    "TODO(brettw) write this.";

Value RunTest(Scope* scope,
              const FunctionCallNode* function,
              const std::vector<Value>& args,
              BlockNode* block,
              Err* err) {
  return ExecuteGenericTarget(functions::kExecutable, scope, function, args,
                              block, err);
}

}  // namespace functions
