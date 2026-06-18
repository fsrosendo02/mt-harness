let
    Source = Csv.Document(
        File.Contents(BaseFolder & "/results.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed = Table.TransformColumnTypes(
        PromotedHeaders,
        {
            {"dataset", type text},
            {"subject_id", type text},
            {"target_id", type text},
            {"run_name", type text},
            {"function_name", type text},
            {"mutant_id", type text},
            {"mutant_hash", type text},
            {"build_status", type text},
            {"test_status", type text},
            {"killed", type text},
            {"executable", type logical},
            {"log_path", type text},
            {"operator", type text},
            {"mutation_descriptor", type text},
            {"owner", type text},
            {"method", type text},
            {"source_line", Int64.Type},
            {"instruction_index", Int64.Type},
            {"original_expression", type text},
            {"replacement_expression", type text},
            {"raw_mutant", type text}
        }
    ),
    CleanKilled = Table.ReplaceValue(Typed, "", null, Replacer.ReplaceValue, {"killed"}),
    CleanTestStatus = Table.ReplaceValue(CleanKilled, "", null, Replacer.ReplaceValue, {"test_status"}),
    AddedKilledFlag = Table.AddColumn(CleanTestStatus, "killed_flag", each if [killed] = "True" then 1 else 0, Int64.Type),
    AddedExecutableFlag = Table.AddColumn(AddedKilledFlag, "executable_flag", each if [executable] = true then 1 else 0, Int64.Type),
    AddedBuildSuccessFlag = Table.AddColumn(AddedExecutableFlag, "build_success_flag", each if [build_status] = "SUCCESS" then 1 else 0, Int64.Type)
in
    AddedBuildSuccessFlag
