let
    Source = Json.Document(File.Contents(BaseFolder & "/run_summary.json")),
    ToRecord = Record.ToTable(Source),
    Expanded = Table.RenameColumns(ToRecord, {{"Name", "field"}, {"Value", "value"}}),
    ResultsOnly = Table.SelectRows(Expanded, each [field] <> "results")
in
    ResultsOnly
