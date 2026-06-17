let
    Results = ResultsCsv,
    Grouped = Table.Group(
        Results,
        {"target_id", "subject_id", "function_name"},
        {
            {"mutant_count", each Table.RowCount(_), Int64.Type},
            {"build_success_rows", each List.Sum([build_success_flag]), Int64.Type},
            {"executable_true_rows", each List.Sum([executable_flag]), Int64.Type},
            {"killed_true_rows", each List.Sum([killed_flag]), Int64.Type},
            {"operators_distinct", each List.Count(List.Distinct([operator])), Int64.Type},
            {"min_source_line", each List.Min([source_line]), Int64.Type},
            {"max_source_line", each List.Max([source_line]), Int64.Type}
        }
    ),
    JoinedTargets = Table.NestedJoin(Grouped, {"target_id"}, TargetsCsv, {"target_id"}, "target_info", JoinKind.LeftOuter),
    ExpandedTargets = Table.ExpandTableColumn(
        JoinedTargets,
        "target_info",
        {"subject", "function", "file", "start_line", "end_line", "status", "generated_mutants", "total_elapsed_sec", "compile_failed_flag", "has_mutants_flag"},
        {"subject", "function", "file", "start_line", "end_line", "status", "generated_mutants", "total_elapsed_sec", "compile_failed_flag", "has_mutants_flag"}
    ),
    AddedBuildRate = Table.AddColumn(ExpandedTargets, "build_success_rate", each if [mutant_count] = 0 then null else Number.From([build_success_rows]) / [mutant_count], Percentage.Type),
    AddedExecRate = Table.AddColumn(AddedBuildRate, "executable_rate", each if [mutant_count] = 0 then null else Number.From([executable_true_rows]) / [mutant_count], Percentage.Type),
    AddedKillRate = Table.AddColumn(AddedExecRate, "kill_rate", each if [mutant_count] = 0 then null else Number.From([killed_true_rows]) / [mutant_count], Percentage.Type),
    Sorted = Table.Sort(AddedKillRate, {{"mutant_count", Order.Descending}, {"target_id", Order.Ascending}})
in
    Sorted
