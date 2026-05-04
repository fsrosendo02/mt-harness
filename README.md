# MT Harness

Harness para gerar, executar e analisar mutantes sobre alvos de código, com foco atual em `Defects4J`.

O repositório suporta três fluxos principais:

- gerar mutantes com LLM e executá-los (`full`)
- gerar mutantes sem executar (`generate_only`)
- executar mutantes já gerados numa run anterior (`execute_only`)

Também inclui utilitários para construir catálogos de alvos, mapear testes que cobrem cada alvo, reconstruir índices e gerar relatórios.

## Visão Geral

O fluxo normal do projeto é:

1. criar ou escolher um catálogo de targets
2. garantir o mapeamento `target -> testes que cobrem o target`
3. correr uma run individual com `run_llm.py` ou um batch com `run_batch.py`
4. consultar os resultados em `harness/executions/runs/`, `harness/executions/batches/` e `harness/reports/`

## Estrutura do Repositório

```text
.
├── configs/                      # exemplos de configuração
├── harness/
│   ├── adapters/                 # integração com benchmarks, ex. Defects4J
│   ├── datasets/
│   │   ├── catalogs/             # catálogos de targets
│   │   └── coverage/             # mapeamentos target_tests.csv
│   ├── executions/
│   │   ├── runs/                 # runs individuais
│   │   └── batches/              # manifestos de batches
│   ├── llm/                      # parsing, prompts e providers
│   ├── reporting/                # índices, summaries e kill matrices
│   └── targets/                  # descoberta, validação e cobertura de targets
├── prompts/                      # prompts usados na geração
├── scripts/                      # helpers manuais
├── tests/                        # testes automáticos
├── run_llm.py                    # entrypoint para uma run individual
├── run_batch.py                  # entrypoint para batches
└── summarize_results.py          # resumo rápido de um results.csv
```

## Pré-requisitos

Antes de usar o harness, o ambiente deve ter:

- `python3`
- `openjdk-11`
- `defects4j`
- `git`, `svn`, `ctags`
- o provider de LLM que vais usar

Providers suportados no repositório:

- `ollama`: usa o comando `ollama run <modelo>`
- `ollama_api`: usa a biblioteca Python `ollama`
- `gpt4o`: usa o binário externo `gpt_run`
- `gemini`: usa o binário externo `gemini_run`

O `Dockerfile` já documenta um ambiente base com `Defects4J`, Java, `ollama` e dependências do sistema.

## Preparação do Ambiente

Exemplo de preparação manual mínima:

```bash
python3 --version
java -version
defects4j pids
ctags --version
```

Se fores usar Ollama:

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

Se fores usar `gpt4o` ou `gemini`, confirma que os wrappers externos existem no `PATH`:

```bash
which gpt_run
which gemini_run
```

## Configurações

Os ficheiros em `configs/` são a forma principal de controlar uma execução.

Exemplos já incluídos:

- `configs/sample_config_full_pipeline.json`: gera e executa mutantes
- `configs/sample_config_generation.json`: gera mutantes sem executar
- `configs/sample_config_execution.json`: reexecuta um batch já existente
- `configs/debugging/test_gpt.json`: run manual simples com provider `gpt4o`
- `configs/debugging/test_gemini.json`: run manual simples com provider `gemini`

Campos importantes mais usados:

- `dataset`: dataset alvo, hoje o fluxo principal assume `defects4j`
- `catalog_file`: catálogo JSON com os targets
- `target_id`: target específico dentro do catálogo
- `subject`: sujeito do benchmark, por exemplo `Lang_1`
- `version`: versão do sujeito, normalmente `f`
- `file` e `function`: alvo manual quando não usas `target_id`
- `model`: nome do modelo
- `provider` ou `model_provider`: provider a usar
- `prompt_file`: prompt de geração
- `num_mutants`: número de mutantes pedidos ao modelo
- `timeout`: timeout de geração por chamada
- `pipeline_mode`: `full`, `generate_only` ou `execute_only`
- `run_mode`: `fresh`, `overwrite` ou `resume`, conforme o contexto
- `mutant_workers`: paralelismo na execução dos mutantes
- `missing_target_tests_policy`: `fail` ou `report_and_skip` para distinguir erro de configuração de gap de cobertura
- `cleanup_tmp`: limpa diretórios temporários no fim
- `validate_after_run`: valida artefactos após a execução
- `rebuild_index`: reconstrói o índice global após a run

## Comandos Principais

### 1. Correr uma run individual

```bash
python3 run_llm.py configs/sample_config_full_pipeline.json
```

O que faz:

- lê um ficheiro JSON de configuração
- resolve o target
- gera mutantes com o provider configurado, se o modo incluir geração
- executa build e testes sobre cada mutante, se o modo incluir execução
- grava resultados, artefactos e metadados da run

Usa este comando quando queres testar um target específico ou uma configuração isolada.

### 2. Correr um batch de targets

```bash
python3 run_batch.py configs/sample_config_full_pipeline.json
```

O que faz:

- lê uma configuração base
- expande essa configuração para todos os targets do catálogo
- cria um `batch_id`
- lança várias runs individuais, uma por target
- escreve o manifesto do batch em `harness/executions/batches/`

Usa este comando quando queres processar um catálogo inteiro ou fazer várias runs por target.

### 3. Gerar mutantes sem executar

```bash
python3 run_llm.py configs/sample_config_generation.json
```

O que faz:

- executa apenas a fase de geração
- guarda os mutantes aceites e rejeitados
- não corre build nem testes

Útil para inspecionar a qualidade da geração antes de gastar tempo na execução.

### 4. Executar mutantes já gerados

```bash
python3 run_batch.py configs/sample_config_execution.json
```

O que faz:

- reutiliza um batch anterior através de `source_batch_id` ou `source_batch_manifest`
- relança as runs em modo `execute_only`
- executa os mutantes já guardados nas runs originais

Útil quando a geração já foi feita e só queres repetir ou ajustar a execução.

## Comandos de Apoio

### Resumir um `results.csv`

```bash
python3 summarize_results.py harness/executions/runs/<run_name>/execution/results.csv
```

O que faz:

- lê um `results.csv`
- deduplica entradas por defeito
- imprime um resumo agregado
- pode também gravar `summary.json`

Exemplo com JSON de saída:

```bash
python3 summarize_results.py harness/executions/runs/<run_name>/execution/results.csv --json-out /tmp/summary.json
```

### Reconstruir `batch manifest` a partir de runs antigas

```bash
python3 scripts/rebuild_batch_manifests.py
```

O que faz:

- lê os `run_manifest.json` dentro de `harness/executions/runs/`
- agrupa as runs por `extra_metadata.batch_id`
- recria `harness/executions/batches/batchXX.json`

Útil quando tens runs antigas no disco mas perdeste os manifestos de batch necessários para `execute_only`.

### Construir um catálogo de targets do Defects4J

```bash
python3 -m harness.targets.build_defects4j_catalog \
  --output harness/datasets/catalogs/defects4j_lang_catalog.json \
  --projects Lang \
  --bug-ids 1,2,3 \
  --versions f \
  --max-per-project 20 \
  --max-per-function 1 \
  --max-per-file 2
```

O que faz:

- faz checkout dos projetos/bugs pedidos
- extrai métodos Java candidatos
- calcula score heurístico para priorização
- gera um catálogo JSON pronto a usar em batches

### Validar um catálogo

```bash
python3 -m harness.targets.validation harness/datasets/catalogs/defects4j_final_catalog.json
```

O que faz:

- valida o formato do catálogo
- verifica campos obrigatórios
- valida `target_id`, linhas e duplicados

### Criar templates `target_tests.csv`

```bash
python3 -m harness.targets.test_coverage_templates
```

O que faz:

- percorre os catálogos em `harness/datasets/catalogs/*.json`
- cria um `target_tests.csv` por catálogo dentro de `harness/datasets/coverage/catalogs/`
- prepara a estrutura para preencher os testes que cobrem cada target
- em modo normal, não acrescenta linhas ao CSV existente; prepara ou reescreve o ficheiro template conforme os argumentos

Para forçar reescrita:

```bash
python3 -m harness.targets.test_coverage_templates --force
```

Para combinar os CSVs preenchidos num índice global:

```bash
python3 -m harness.targets.test_coverage_templates --combine
```

Exemplo para o catálogo pilot:

```bash
python3 -m harness.targets.test_coverage_templates harness/datasets/catalogs/defects4j_pilot_catalog.json
```

Nota sobre o formato:

- o template inicial cria uma linha por target do catálogo
- depois da recolha de cobertura, o mesmo `target_id` pode aparecer em várias linhas
- isso é esperado, porque existe uma linha por par `target -> teste`

### Descobrir automaticamente testes que cobrem cada target

```bash
python3 -m harness.targets.test_coverage_collection harness/datasets/catalogs/defects4j_final_catalog.json
```

O que faz:

- faz checkout dos subjects do catálogo
- enumera os testes do projeto
- corre cobertura com `defects4j coverage`
- identifica quais testes tocam as linhas do target
- regenera por completo o `target_tests.csv` do catálogo no fim da execução

Detalhes importantes:

- o output é `overwrite`, não `append`: o CSV por catálogo é sempre reescrito como snapshot consistente
- durante a execução, o collector já grava checkpoints intermédios no `target_tests.csv`, por isso consegues ver progresso enquanto corre
- os logs desta recolha ficam em `logs/coverage/`
- `ok=False` no log quer dizer apenas que essa invocação de `defects4j coverage` não produziu cobertura utilizável; esse teste é ignorado para o mapeamento final
- a resolução de testes pode vir diretamente de `tests.all` ou de expansão `classe -> métodos`, dependendo do que o subject exporta
- o matching de cobertura usa classe, ficheiro e linhas do target; quando o XML só permite um matching mais fraco, o collector emite `WARN fallback XML match`
- o CSV final inclui `match_mode`, para distinguires matches fortes (`strict_class_and_file`) de matches mais fracos
- por defeito, o collector reutiliza checkouts já existentes; usa `--no-reuse-checkout` se precisares de forçar checkout limpo
- não precisas de passar `--reuse-checkout` explicitamente, porque esse já é o comportamento default
- se usares `--clean`, o diretório de trabalho do catálogo é apagado antes de começar, o que na prática invalida a reutilização desse catálogo

Este comando é importante porque a execução falha de forma estrita se não houver mapeamento de testes para o target.

Exemplo para o catálogo pilot:

```bash
python3 -m harness.targets.test_coverage_collection harness/datasets/catalogs/defects4j_pilot_catalog.json
```

Fluxo completo para preparar o mapa de testes do pilot:

```bash
python3 -m harness.targets.test_coverage_templates harness/datasets/catalogs/defects4j_pilot_catalog.json
python3 -m harness.targets.test_coverage_collection harness/datasets/catalogs/defects4j_pilot_catalog.json
python3 -m harness.targets.test_coverage_templates --combine
```

Durante a recolha de coverage, podes acompanhar o progresso por teste em:

- `logs/coverage/<catalog>__coverage_<timestamp>.log`

### Reconstruir o índice global de experiências

```bash
python3 -m harness.reporting.experiment_index
```

O que faz:

- percorre `harness/executions/runs/`
- lê `run_manifest.json`, `results.csv` e `summary.json`
- agrega tudo em `harness/reports/experiment_index.csv`

### Gerar kill matrices

```bash
python3 -m harness.reporting.kill_matrix --group-by project
```

O que faz:

- lê os resultados estruturados das runs
- gera matrizes de mutante x teste
- escreve CSVs por `project`, `subject` ou `run`

Exemplo por run:

```bash
python3 -m harness.reporting.kill_matrix --group-by run
```

### Resumir um batch

```bash
python3 -m harness.reporting.summarize_batch --batch-id batch01
```

O que faz:

- lê o manifesto do batch
- agrega geração, execução e rejeições
- escreve relatórios em `harness/reports/batch_summaries/`

### Executar mutantes manuais

```bash
python3 -m harness.executions.manual_mutants configs/debugging/manual_mutants_sample.json
```

O que faz:

- carrega mutantes definidos manualmente em JSON
- ignora a fase de geração por LLM
- corre o pipeline de execução normal sobre esses mutantes

Útil para debugging e validação do runner.

### Smoke test manual do Defects4J

```bash
python3 scripts/test_defects4j_smoke.py
```

O que faz:

- corre um teste manual rápido ao comportamento base do `Defects4J`
- serve como sanity check do ambiente

## Onde Ficam os Resultados

Resultados e artefactos principais:

- `harness/executions/runs/<run_name>/`
- `harness/executions/runs/<run_name>/generation/`
- `harness/executions/runs/<run_name>/execution/results.csv`
- `harness/executions/runs/<run_name>/execution/test_results.csv`
- `harness/executions/runs/<run_name>/execution/summary.json`
- `harness/executions/batches/batchNN.json`
- `harness/reports/experiment_index.csv`
- `harness/reports/kill_matrices/`

## Fluxos Recomendados

### Fluxo A: correr um catálogo completo

1. validar o catálogo
2. criar os templates `target_tests.csv`
3. recolher cobertura para preencher os testes por target
4. combinar os mapeamentos num índice global, se necessário
5. correr `run_batch.py`
6. gerar índice e kill matrices

Comandos:

```bash
python3 -m harness.targets.validation harness/datasets/catalogs/defects4j_final_catalog.json
python3 -m harness.targets.test_coverage_templates
python3 -m harness.targets.test_coverage_collection harness/datasets/catalogs/defects4j_final_catalog.json
python3 -m harness.targets.test_coverage_templates --combine
python3 run_batch.py configs/sample_config_full_pipeline.json
python3 -m harness.reporting.experiment_index
python3 -m harness.reporting.kill_matrix --group-by project
```

### Fluxo B: testar um target específico

1. editar um ficheiro JSON em `configs/debugging/`
2. correr `run_llm.py`
3. inspecionar `results.csv`, `test_results.csv` e `summary.json`

Comando:

```bash
python3 run_llm.py configs/debugging/test_gpt.json
```

### Fluxo C: validar o runner sem LLM

```bash
python3 -m harness.executions.manual_mutants configs/debugging/manual_mutants_sample.json
```

## Testes

Teste automático atualmente presente no repositório:

```bash
python3 -m unittest tests.test_kill_matrix_pipeline
```

O que valida:

- falha rápida quando falta o mapeamento de target tests
- geração correta de `test_results.csv`
- construção de kill matrices a partir dos resultados estruturados

## Notas Importantes

- `run_llm.py` e `run_batch.py` esperam sempre um ficheiro de configuração JSON como primeiro argumento.
- `run_llm.py --help` e `run_batch.py --help` não funcionam como CLI tradicional porque os scripts leem diretamente `sys.argv[1]` como path de config.
- o modo `execute_only` exige `run_name` numa run individual ou `source_batch_id`/`source_batch_manifest` num batch.
- a infraestrutura de execução distingue entre `target_tests.csv` em falta e targets sem testes mapeados; com `missing_target_tests_policy: "report_and_skip"`, a run fecha com estado `no_coverage`.
- o repositório hoje está centrado em `Defects4J`, mesmo que a estrutura interna já esteja preparada para abstrações por dataset.

## Ficheiros de Referência

- [configs/sample_config_full_pipeline.json](/home/francisco/mt-harness/configs/sample_config_full_pipeline.json)
- [configs/sample_config_generation.json](/home/francisco/mt-harness/configs/sample_config_generation.json)
- [configs/sample_config_execution.json](/home/francisco/mt-harness/configs/sample_config_execution.json)
- [harness/datasets/catalogs/defects4j_final_catalog.json](/home/francisco/mt-harness/harness/datasets/catalogs/defects4j_final_catalog.json)
- [harness/datasets/coverage/README.md](/home/francisco/mt-harness/harness/datasets/coverage/README.md)
