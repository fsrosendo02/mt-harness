let
    Targets = TargetsCsv,
    Results = ResultsCsv,
    TargetsGrouped = Table.Group(
        Targets,
        {"subject"},
        {
            {"target_count", each Table.RowCount(_), Int64.Type},
            {"generated_mutants_total", each List.Sum([generated_mutants]), Int64.Type},
            {"nonzero_mutant_targets", each List.Sum([has_mutants_flag]), Int64.Type},
            {"compile_failed_targets", each List.Sum([compile_failed_flag]), Int64.Type},
            {"avg_elapsed_sec", each List.Average([total_elapsed_sec]), type number},
            {"max_elapsed_sec", each List.Max([total_elapsed_sec]), type number}
        }
    ),
    ResultsGrouped = Table.Group(
        Results,
        {"subject_id"},
        {
            {"mutant_count", each Table.RowCount(_), Int64.Type},
            {"build_success_rows", each List.Sum([build_success_flag]), Int64.Type},
            {"executable_true_rows", each List.Sum([executable_flag]), Int64.Type},
            {"killed_true_rows", each List.Sum([killed_flag]), Int64.Type},
            {"distinct_targets_in_results", each List.Count(List.Distinct([target_id])), Int64.Type},
            {"distinct_operators", each List.Count(List.Distinct([operator])), Int64.Type}
        }
    ),
    Joined = Table.NestedJoin(TargetsGrouped, {"subject"}, ResultsGrouped, {"subject_id"}, "result_info", JoinKind.LeftOuter),
    Expanded = Table.ExpandTableColumn(
        Joined,
        "result_info",
        {"mutant_count", "build_success_rows", "executable_true_rows", "killed_true_rows", "distinct_targets_in_results", "distinct_operators"},
        {"mutant_count", "build_success_rows", "executable_true_rows", "killed_true_rows", "distinct_targets_in_results", "distinct_operators"}
    ),
    Filled = Table.ReplaceValue(Expanded, null, 0, Replacer.ReplaceValue, {"mutant_count", "build_success_rows", "executable_true_rows", "killed_true_rows", "distinct_targets_in_results", "distinct_operators"}),
    AddedPctNonZero = Table.AddColumn(Filled, "pct_targets_with_mutants", each if [target_count] = 0 then null else Number.From([nonzero_mutant_targets]) / [target_count], Percentage.Type),
    AddedAvgMutantsPerTarget = Table.AddColumn(AddedPctNonZero, "avg_mutants_per_target", each if [target_count] = 0 then null else Number.From([generated_mutants_total]) / [target_count], type number),
    Sorted = Table.Sort(AddedAvgMutantsPerTarget, {{"generated_mutants_total", Order.Descending}, {"subject", Order.Ascending}})
in
    Sorted
