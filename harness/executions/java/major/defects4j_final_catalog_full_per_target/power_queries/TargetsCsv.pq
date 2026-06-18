let
    Source = Csv.Document(
        File.Contents(BaseFolder & "/targets.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed = Table.TransformColumnTypes(
        PromotedHeaders,
        {
            {"target_id", type text},
            {"subject", type text},
            {"function", type text},
            {"file", type text},
            {"start_line", Int64.Type},
            {"end_line", Int64.Type},
            {"status", type text},
            {"mmlc_returncode", Int64.Type},
            {"checkout_returncode", Int64.Type},
            {"compile_returncode", Int64.Type},
            {"generated_mutants", Int64.Type},
            {"mutants_log_count", Int64.Type},
            {"mmlc_elapsed_sec", type number},
            {"checkout_elapsed_sec", type number},
            {"compile_elapsed_sec", type number},
            {"total_elapsed_sec", type number},
            {"target_dir", type text},
            {"summary_json", type text},
            {"log_file", type text}
        }
    ),
    AddedNonZeroFlag = Table.AddColumn(Typed, "has_mutants_flag", each if [generated_mutants] > 0 then 1 else 0, Int64.Type),
    AddedCompileFailedFlag = Table.AddColumn(AddedNonZeroFlag, "compile_failed_flag", each if [status] = "compile_failed" then 1 else 0, Int64.Type),
    AddedOkFlag = Table.AddColumn(AddedCompileFailedFlag, "ok_flag", each if [status] = "ok" then 1 else 0, Int64.Type)
in
    AddedOkFlag
