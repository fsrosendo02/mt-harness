# Plano de Implementação: `mull` como Baseline de Mutação para Alvos C (ManyBugs)

## Objetivo

Adicionar o [`mull`](https://mull.readthedocs.io/) (mutation testing baseado em
LLVM/Clang) como ferramenta de baseline para os alvos em C do `ManyBugs`,
desempenhando para o stack C o mesmo papel que o `Major` já desempenha para o
stack Java: uma fonte de mutantes "de ferramenta" independente dos mutantes
gerados por LLM, usada para comparação (fault coupling, triviality, DMR, kill
matrix).

Este plano assume que o Phase 8 ("Optional Baseline with Mull") do
`manybugs_integration_plan.md` já foi alcançado, ou seja, que o pipeline
nativo `ManyBugsAdapter` + `MutationRunner` já roda mutantes LLM em pelo menos
um catálogo C ponta a ponta. Não deve ser iniciado antes disso.

## Por que não reusar o `MutationRunner` diretamente

O `MutationRunner`/`MutantEvaluator` atuais assumem um mutante = uma
substituição de código completa (`Mutant.code`) aplicada a um `workdir`
clonado, com build+test rodando em um container Docker recriado por mutante
(`ManyBugsAdapter.build`/`test_target`). Isso é adequado para poucas dezenas
de mutantes LLM por alvo, mas é o oposto do modelo de execução do `mull`:

- `mull` instrumenta a compilação uma única vez (via plugin LLVM/Clang ou
  IR mutation) e depois executa *todos* os mutantes de uma unidade de
  compilação contra o binário de teste, reaproveitando build.
- Recriar um container Docker por mutante do `mull` (que tipicamente gera
  centenas de mutantes por arquivo) seria proibitivamente lento e desperdiça
  exatamente a otimização que o `mull` oferece.

Isso é análogo à decisão já tomada para o `Major`: ele roda como pipeline
offline separado (`scripts/run_major_subject.py`, `run_major_catalog.py`,
`compute_major_baseline_analysis.py`), fora do `MutationRunner`, e os
resultados são normalizados depois para CSVs no mesmo formato para permitir
comparação. O `mull` deve seguir exatamente esse padrão, adaptado a C.

## Diferença de Operação em Relação ao Major (Java) — Decisão Registrada

Foi levantada a pergunta de se dava para replicar para C o mesmo padrão já
usado para Java, onde `scripts/run_major_exported_mutants.py` pega mutantes
*já gerados como arquivos-fonte reais* pelo Major e os injeta no
`MutationRunner`/`MutantEvaluator` normal (`manual_mutants` pipeline), dando
build+teste por mutante idêntico ao caminho do LLM.

Isso funciona para o Major porque ele muta no nível de **AST/source Java** e
tem exportação nativa de cada mutante como um `.java` real e compilável. Para
o `mull`, essa premissa é mais fraca, mas não é categoricamente falsa: a
mutação em si acontece **em memória, no nível de LLVM bitcode/IR**
(confirmado em [How Mull Works](https://mull.readthedocs.io/en/0.18.0/HowMullWorks.html)),
com precisão de mapeamento para source inferior à de um mutador nativo de
AST. Mas o `mull` 0.18.0 expõe um reporter `Patches`
(`--reporters=Patches`, ver
[CLI do mull-runner](https://mull.readthedocs.io/en/0.18.0/command-line/mull-runner.html))
que reconstrói patches unidiff a partir do IR mutado para (pelo menos parte
d)os mutadores. **Não avaliado ainda** se esses patches aplicam de forma
limpa ao source original do `gzip`/ManyBugs, nem se cobrem todos os
mutadores de interesse ou só um subconjunto — isso é um item de investigação
real antes de descartar a ideia, não uma impossibilidade confirmada.

**Decisão (mantida, mas com base revista):** seguir com o pipeline offline
(`mull-runner` builda uma vez, roda todos os mutantes do arquivo) como
arquitetura principal, por desempenho — não porque seja "a única
tecnicamente possível". Se o reporter `Patches` se mostrar utilizável, um
bridge para o `MutationRunner` via `manual_mutants` (como já existe para o
Major) passa a ser uma opção secundária a reavaliar, não algo descartado de
saída.

| | Major (Java) | `mull` (C) |
|---|---|---|
| Nível de mutação | AST/source (plugin do `javac`) | LLVM bitcode/IR, em memória |
| Representação primária do mutante | Arquivo `.java` real por mutante | Ponto de mutação no IR; patch reconstruído via reporter `Patches` é secundário e não avaliado |
| Quem builda/testa cada mutante | `MutationRunner`/`MutantEvaluator`, 1 container por mutante | `mull-runner`, 1 build para todos os mutantes do arquivo |
| Onde a uniformidade acontece | Motor de execução (mesmo código que roda LLM) | Schema de resultado (normalização pós-execução para `MutantResult`/`TestObservation`) |
| Granularidade por teste | Nativa do `ManyBugsAdapter`/`test_target` | Uma execução `mull-runner` por `test_id` elegível, agregada depois — ver "Etapa D" no risco de fidelidade de runtime, abaixo |
| Oráculo de testes | `target_tests_for()` | O mesmo `target_tests_for()` — isto sim é idêntico entre os dois |

Ou seja: **o que fica uniforme entre Java e C é o formato de saída e o
oráculo de testes usado, não (necessariamente) o motor de execução do
mutante**. O pipeline offline é suficiente para as análises de comparação
(fault coupling, DMR, kill matrix) que consomem os CSVs normalizados. Um
bridge direto para o `MutationRunner` fica em aberto como possibilidade
futura, condicionada à avaliação do reporter `Patches`, não fechada.

## Posição na Arquitetura

```
harness/executions/c/llm/<batch>/...        (já existe — mutantes LLM)
harness/executions/c/mull/                  (novo — baseline mull)
    execution/<run_name>/results.csv
    execution/<run_name>/test_results.csv
    mutant_summary_mull.csv
    kill_matrix_long_mull.csv
    mull_dmr_detail.csv
```

Isso espelha `harness/executions/java/major/`. Nenhuma mudança é necessária
no caminho principal do `MutationRunner` nem no `ManyBugsAdapter` usado pelo
LLM.

## Desafio central: toolchain

As imagens `prosyslab/manybugs:<scenario>` foram construídas com toolchains
antigos e `gcc`/`make`/`autoconf` específicos de cada projeto (gzip,
lighttpd, libtiff, python, gmp). O `mull` exige Clang/LLVM para produzir
bitcode/IR instrumentado. Isso significa que **não dá para rodar o `mull`
dentro da imagem original sem modificação**.

### Achados empíricos (PoC real, 2026-07-21, alvo `gzip-2009-08-16-...`)

Um spike real (containers Docker de verdade, não especulação) testou duas
hipóteses de ambiente base para a imagem derivada:

**Tentativa 1 — Ubuntu 22.04 (jammy), glibc 2.35: falhou.**
`mull-14`/Clang 14 instalam sem atrito via `apt` nativo do jammy (nem precisa
do repositório `apt.llvm.org`). Mas a build do `gzip` falha, **com Clang e
com GCC igualmente** — não é um problema de compilador, é um problema de
**glibc**: o snapshot de `gnulib` embutido no `gzip` (~2009–2013) tem, em
`lib/freadahead.c`, um `#if defined _IO_ftrylockfile` que dependia de
símbolos internos do glibc (`_IO_IN_BACKUP`, `_IO_save_base`, etc.,
declarados historicamente em `libio.h`). O glibc ≥2.28 (jammy tem 2.35) não
expõe mais esses símbolos/header publicamente, então a macro de fallback do
`gnulib` cai no `#error "Please port gnulib freadahead.c to your platform!"`
independentemente do compilador. Corrigir isso exigiria patch não-trivial no
`gnulib` vendorizado (não é 1 linha — as structs internas do glibc moderno
não são mais visíveis via include público).

**Tentativa 2 — Ubuntu 18.04 (bionic), glibc 2.27: funcionou.**
`bionic` ainda expõe `/usr/include/libio.h` e `_IO_IN_BACKUP` publicamente
(a era do `gnulib` embutido bate com a era do glibc do bionic), então a
build do `gzip` compilou **limpo com `clang-9`** (via `./configure
CC=clang-9` + `make`), produzindo um executável ELF64 funcional. O
repositório Cloudsmith do `mull` também publica pacotes para `bionic`
(`mull-6` a `mull-10`, cobrindo LLVM 6–10) — instalado e testado
`mull-9`/0.18.0 lado a lado com `clang-9` sem conflito.

**Conclusão prática:** a imagem derivada correta **não é** `FROM
prosyslab/manybugs:<scenario>` (Xenial/glibc 2.23, `apt` já não funciona
nessas imagens — testado, dá erro de permissão/repos mortos) **nem** uma
base Ubuntu moderna (glibc novo demais para o `gnulib` vendorizado). O ponto
doce é **Ubuntu 18.04 (bionic)**: extrai-se o source de dentro da imagem
`prosyslab/manybugs` (como o `ManyBugsAdapter.checkout_subject` já faz) e
builda-se numa imagem bionic separada com `clang-N`/`mull-N` da faixa 6–10
instalados via `apt`+Cloudsmith. Isso substitui a suposição original de
"LLVM 15–18 numa imagem derivada da própria imagem ManyBugs" — era otimista
demais sobre a idade do código-fonte destes alvos.

Isso não está validado ainda para `lighttpd`, `libtiff`, `python`, `gmp` —
cada um pode ter seu próprio ponto doce de glibc/LLVM (`python`, sendo maior
e mais moderno, pode tolerar um glibc mais novo que `gzip`; `gmp` com
assembly inline continua sendo o candidato mais arriscado). Isso deve ser
revalidado por família antes de generalizar a Fase 1.

### Nota menor: `compile_commands.json` via `bear`

`bear --use-cc=clang-9 make` capturou `compile_commands.json`, mas algumas
entradas registram o comando como `cc` em vez de `clang-9`. Não bloqueou o
PoC (a build real dos `.o` de topo usou mesmo `clang-9`, confirmado pelos
warnings específicos de Clang no log), mas deve ser corrigido antes de
automatizar a Fase 2 — provável causa é uma sub-regra do `Makefile` do
`gzip` usando `cc` bruto em vez de `$(CC)`. Não foi mais investigado porque
a Fase 2 real (ver abaixo) acabou não precisando de `compile_commands.json`
— builda-se direto com `CC=clang-9 CFLAGS="..." make`.

### Fluxo real do `mull` validado ponta-a-ponta (2026-07-21) — funciona

Depois de mais investigação (incluindo clonar o código-fonte do `mull` e
achar a config `lit` da própria tag `0.18.0`, que bate exatamente com a
versão instalada via Cloudsmith no bionic), o fluxo real ficou claro e foi
**validado com um mutante de verdade, morto de verdade**:

1. **Compilação instrumentada**, num único comando (compilar+linkar juntos
   ou em separado, tanto faz): 
   ```
   clang-9 -O1 -fexperimental-new-pass-manager \
     -fpass-plugin=/usr/lib/mull-ir-frontend-9 \
     -g -grecord-command-line <fontes> -o <exe>
   ```
   **Os dois flags `-O1` e `-fexperimental-new-pass-manager` são
   obrigatórios para LLVM 9/10** — sem eles, o `-fpass-plugin=` carrega mas
   o passe de mutação simplesmente não roda (falha silenciosa: build passa,
   `mull-runner` reporta "No mutants found", sem nenhum erro). A necessidade
   de `-O1` em si já está documentada no tutorial oficial ("Hello World" do
   Mull 0.17.1, que usa `-O1` no exemplo Clang 9/10/11 — ver
   https://mull.readthedocs.io/en/0.17.1/tutorials/HelloWorld.html); o que
   não achei documentado publicamente foi a combinação exata com
   `-fexperimental-new-pass-manager` e a explicação do porquê — isso só
   apareceu ao ler `tests-lit/lit.cfg` do próprio código-fonte na tag
   `0.18.0`, com o comentário explícito: `# LLVM 9 and 10 doesn't include
   the pass if no optimizations enabled`. Versões mais novas do `mull`
   (LLVM ≥12, série `mull-14`/`mull-19` do jammy) não precisam disso — usam
   só `-fpass-plugin=` sozinho, já que o "new pass manager" virou default.
2. **`mull.yml`** na mesma pasta de onde se compila, com pelo menos a chave
   `mutators` (lista de nomes como `cxx_add_to_sub`, `cxx_lt_to_le`, etc. —
   lista completa via `mull-runner-9 --dump-mutators`).
3. **Execução**: `mull-runner-N <exe-instrumentado> --test-program=<caminho>
   [-ide-reporter-show-killed] [--reporters=SQLite,IDE]
   [--report-dir=<dir>]`. O `<exe-instrumentado>` posicional é usado para
   descoberta de mutantes/cobertura; `--test-program` é o que decide
   passou/falhou — pode ser um programa completamente diferente do binário
   mutado (ex. um wrapper bash/python que por sua vez invoca o binário
   mutado), o que cobre exatamente o caso ManyBugs (`gzip` mutado é chamado
   indiretamente pelo `test.sh`/Perl).
4. **Validado com um exemplo sintético mínimo**
   (`int add(int a,int b){return a+b;}`): o `mull-runner-9` reportou
   corretamente `Killed: Replaced + with - [cxx_add_to_sub]` na linha/coluna
   certas, com mutation score 100%. O mecanismo fim-a-fim funciona.

Isso substitui a suposição de que faltava descobrir "como o mull-runner
invoca o teste" — já não falta: está resolvido e documentado aqui.

## Fases

### Fase 0 — Spike de viabilidade (1 alvo, manual)

**Meta:** confirmar que dá para compilar pelo menos um projeto do ManyBugs
com Clang+mull e rodar mutação IR contra os testes existentes.

**Status: parcialmente concluída (PoC real em 2026-07-21).** Já validado:

- Alvo escolhido: `gzip-2009-08-16-...` (`huft_build`, `inflate.c`, já tem
  `target_tests.csv` com testes `n1/p1/p2` curados — ver
  `harness/datasets/coverage/catalogs/manybugs_gzip_pilot/target_tests.csv`).
- Ambiente correto **não** é `FROM prosyslab/manybugs:<scenario>` nem uma
  imagem Ubuntu moderna — é **Ubuntu 18.04 (bionic)**, com o source do
  `gzip` extraído da imagem original e copiado para dentro (ver seção
  "Achados empíricos" acima para o porquê).
- `clang-9` + `mull-9` (0.18.0) instalam via `apt` nativo do bionic +
  repositório Cloudsmith do `mull`, sem builds manuais de LLVM.
- `./configure CC=clang-9 && CC=clang-9 bear --use-cc=clang-9 make -j2`
  compila o `gzip` inteiro sem erros, produzindo um binário ELF64 real.

Adicionalmente já validado (ver "Fluxo real do `mull` validado
ponta-a-ponta" acima): os flags corretos de instrumentação
(`-O1 -fexperimental-new-pass-manager -fpass-plugin=mull-ir-frontend-9`),
o `mull.yml` com `mutators`, e a invocação `mull-runner-N <exe>
--test-program=<wrapper>` — testado com um mutante sintético real,
corretamente marcado Killed. `compile_commands.json`/`bear` **não** é
necessário nesse fluxo (não é usado em nenhum passo de 1–4 na seção acima);
a nota sobre `cc` vs `clang-9` fica como item cosmético a resolver só se
uma automação futura vier a depender de `compile_commands.json`, não como
bloqueador da Fase 0.

**Status final (2026-07-22): Fase 0 concluída para o alvo piloto.** Ver
seção "RESOLVIDO: causa isolada — não é glibc, é bitness" (dentro de "Risco:
o rebuild em bionic..." abaixo) para o percurso completo. Resumo:

- **Gate de equivalência de runtime**: resolvido para este alvo. A causa da
  divergência observada no PoC anterior (`helin-segv` segfaultando em
  bionic) era ter compilado 64-bit em vez de 32-bit (`-m32`, a flag da
  receita original) — não glibc/allocator. Com `-m32`, bionic reproduz o
  comportamento original (`n1`/`p1`/`p2` passam, batendo com
  `baseline_failures: 0` já confirmado em execuções LLM reais).
- **`mull-runner-9` de verdade, escopado a `inflate.c`**: executado com
  sucesso. 35 mutantes reais no intervalo `292-495` do catálogo
  (`huft_build`), `--test-program` chamando `test.sh n1 dummy`. Encontrado e
  documentado, de caminho, um segundo gotcha de falha silenciosa: `CFLAGS`
  gravado pelo `configure` no `Makefile` tem precedência sobre `CFLAGS`
  passado como variável de ambiente ao `make` — precisa ser passado como
  argumento de linha de comando (`make CFLAGS=...`).

**Critério de saída: atingido para o alvo piloto.** Falta apenas rodar
`p1`/`p2` separadamente e agregar (mecânico, não é mais risco de
viabilidade) e revalidar `-m32`/gate de equivalência por família na Fase 1.

### Fase 1 — Imagens derivadas reproduzíveis

**Meta:** eliminar o passo manual da Fase 0 e torná-lo repetível por família
de projeto.

**Status: concluída para `gzip` (2026-07-30).** Implementado e validado de
verdade (não é desenho, é código que corre):

- [`scripts/manybugs/mull/Dockerfile.gzip`](scripts/manybugs/mull/Dockerfile.gzip):
  parte de `ubuntu:18.04` (bionic); instala `clang-9`, `mull-9` (via
  Cloudsmith), `gcc-multilib` (necessário porque a receita original do
  `gzip` usa `-m32` — ver "RESOLVIDO: causa isolada", **não** assumir que
  as outras famílias também precisam de `-m32`), `make`, `perl`, `psmisc`,
  `sqlite3`. Grava os flags de build corretos (`MULL_CFLAGS`/`MULL_LDFLAGS`,
  incluindo `-m32 -O1 -fexperimental-new-pass-manager
  -fpass-plugin=mull-ir-frontend-9`) como `ENV` da imagem, para
  `prepare_mull_checkout.py` reaproveitar sem repetir a string em dois
  lugares.
- [`scripts/manybugs/mull/prepare_mull_checkout.py`](scripts/manybugs/mull/prepare_mull_checkout.py):
  dado um `--subject-id`, builda (ou reaproveita, via cache do Docker) a
  imagem toolchain, extrai `/experiment` da imagem `prosyslab/manybugs`
  original (mesmo mecanismo de `docker cp` que
  `ManyBugsAdapter.checkout_subject` usa), copia para um container novo da
  imagem toolchain, corre `./configure CC=clang-9` (sem `CFLAGS` — de
  propósito, para não colidir com o `make CFLAGS=...` seguinte) e depois
  `make CC=clang-9 CFLAGS="$MULL_CFLAGS" LDFLAGS="$MULL_LDFLAGS"` **como
  argumentos de linha de comando do `make`**, não variáveis de ambiente
  herdadas (ver achado de engenharia do `CFLAGS`). Verifica a secção
  `.mull_mutants` no binário final antes de declarar sucesso — build que
  "passa" mas não tem essa secção é a falha silenciosa já documentada, não
  deve ser aceite como checkout válido.
- **Validado end-to-end**: rodar o script contra o alvo piloto
  (`gzip-2009-08-16-3fe0caeada-39a362ae9d`) produz `build_ok: true`,
  `mutants_embedded: true`, e o container resultante passa o gate de
  equivalência (`n1`/`p1`/`p2` via `test.sh` todos `PASS`), igual ao
  resultado manual da Fase 0.
- **Não implementado ainda:** `bear`/`compile_commands.json` — removido do
  critério de saída, porque o fluxo real do `mull` (Fase 0) não precisa
  disso; era uma suposição inicial já corrigida.

#### Investigação `lighttpd` (2026-07-31) — EM ABERTO, risco de categoria diferente do `gzip`

Spike real (containers de verdade) para começar a expandir a Fase 1/2 a
`lighttpd`, por pedido explícito do utilizador. Achados até agora:

- Imagem original é **Ubuntu 14.04 (Trusty)**, mais antiga que a do `gzip`
  (Xenial). `CFLAGS` original não tem `-m32` — build é 64-bit nativo, o que
  elimina o risco de arquitetura que dominou a investigação do `gzip`.
- Sistema de build é Autotools (mesmo padrão), mas com dependências reais
  de bibliotecas de desenvolvimento não instaladas por omissão em bionic
  (`libglib2.0-dev` para `gthread-2.0`, `libbz2-dev`) — resolvido,
  `./configure`+`make -j2 CC=clang-9` compila 100% limpo em bionic sem
  patches no código.
- Testes são **Perl TAP (`.t` files) via `Test::Harness`**, não scripts
  shell diretos como no `gzip` — `test.sh` chama `perl
  lighttpd-run-tests.pl <N>`, sem passar por `make <alvo>.log`. A mesma
  técnica de parsing (`test.sh` → `run_test N` → `@tests` array) usada em
  `resolve_test_script_name()` para `gzip` deve funcionar aqui também
  (não testado ainda se o mecanismo de seleção de mutante do `mull`
  sobrevive a esta cadeia diferente — pode ter um problema análogo ao do
  `make`, pode não ter, não verificado).

**Correção metodológica importante (achado inicial estava distorcido):** a
primeira comparação usou `docker run --rm ... bash -c '...'` (container
efémero, novo por teste), que **não é** como o harness real invoca testes —
`ManyBugsAdapter._run_single_test` usa `docker exec -w /experiment
<container> bash test.sh <id> dummy` num container persistente. Repetindo a
comparação com a invocação correta (e depois de perceber que os meus
próprios `rm -rf tmp` entre execuções apagavam
`tests/tmp/lighttpd/servers/www.example.org/pages/`, uma pasta de
infraestrutura referenciada por quase todos os `.conf` de teste — não algo
para limpar, ao contrário do padrão do `gzip`), o quadro real é mais claro
e mais favorável do que a primeira leitura:

- **`n1`–`n12` (12 dos 14 testes elegíveis deste alvo piloto): já são
  testes não-discriminantes/quebrados nesta imagem, independentemente do
  `mull`.** Confirmado contra dados reais de execuções LLM já persistidas
  (`harness/executions/c/llm/batch06/.../lighttpd-1806-1807.../test_results.csv`):
  `n1`–`n12` reportam `FAIL` para **os 8 mutantes de 8** (100%, incluindo o
  próprio baseline) — exatamente o mesmo padrão do `n1`/`hufts` do `gzip`,
  só que aqui afeta 12 testes em vez de 1. Isto não é um risco do `mull`;
  é uma característica pré-existente deste cenário do ManyBugs.
- **`p1` (`mod-rewrite.t`): confirmado equivalente** entre original e
  rebuild bionic+Clang-9, com a invocação correta (`docker exec -w
  /experiment`) — `PASS` em ambos.
- **`p2` (`lowercase.t`): regressão real, confirmada, causa ainda não
  isolada.** Original: `PASS` 10/10. Rebuild: servidor arranca (passa o
  subteste "Starting lighttpd"), mas os pedidos HTTP seguintes recebem
  "Connection refused" — o processo `lighttpd` não é encontrado a correr
  pouco depois de arrancar. Descartadas duas hipóteses falsas levantadas
  durante a depuração manual (`dlopen() failed for mod_indexfile.so` —
  artefacto de reproduzir a invocação do `lighttpd` à mão sem replicar o
  `MODULES_PATH` que `LightyTest.pm` define; não aparece na execução real
  via `test.sh`). Causa raiz por confirmar: pode ser miscompilação
  específica do Clang, pode ser a mesma categoria de risco de bibliotecas
  dinâmicas (`libssl`/`libpcre`/`zlib` — bionic vs. Trusty) que suspeitei
  inicialmente, pode ser outra coisa. **Não investigado ao ponto de
  isolar**, ao contrário do `-m32` do `gzip`.

**Estado prático:** para este alvo piloto (`lighttpd-1806-1807`), o
conjunto de testes elegíveis fica reduzido a **apenas `p1`** como oráculo
confiável hoje (`n1`–`n12` já não discriminam nada, `p2` tem uma regressão
por resolver). Um baseline `mull` com um único teste-oráculo é fraco, mas
não é inválido — só reporta "sobrevive a `p1`" em vez de "sobrevive a
todos os testes elegíveis". Escopar assim (opção escolhida pelo
utilizador) é possível para este alvo específico; os outros 5 alvos do
catálogo `manybugs_lighttpd_pilot` (subject_ids diferentes, cada um com o
seu `test.sh` próprio) **ainda não foram verificados** — o padrão
"a maioria dos `n*` já não discrimina" pode ou não se repetir.

**Não prosseguir com `Dockerfile.lighttpd`/`prepare_mull_checkout` para
`lighttpd` até decidir se `p2` vale a pena depurar mais, ou se escopar só a
`p1` (e o equivalente por alvo nos outros 5 subject_ids) é aceitável.**
Recursos de PoC já limpos.

#### Segundo problema real (2026-07-31): instrumentação do `mull` não fica embutida no binário final de forma fiável

Depois de decidir escopar a `p1` e avançar (`Dockerfile.lighttpd` +
`prepare_mull_checkout.py` generalizados para múltiplos projetos —
`binary_relpath`/`make_jobs` agora parametrizados por família), a
`prepare_mull_checkout.py` real reportou `build_ok: true` mas
`mutants_embedded: false` — o guard já existente (criado durante o `gzip`)
apanhou isto corretamente, mas revelou um problema novo, específico do
`lighttpd`.

Investigação: muitos `.o` individuais têm a secção `.mull_mutants`
(confirmado via `readelf -S`), mas o **binário final ligado não tem
nenhuma**. Isolei uma hipótese (corrida no build paralelo `make -j2`,
build limpo com `-j1` produziu o binário correto numa tentativa manual) e
implementei a correção (`DEFAULT_MAKE_JOBS` por projeto, `lighttpd` →
`-j1`). **A hipótese estava errada, ou insuficiente**: repeti o teste com
`-j1` a partir de um checkout completamente fresco (via o script real, não
manualmente) e o problema **persistiu** — desta vez 13/61 `.o` sem secção
(provavelmente ficheiros triviais sem pontos de mutação para os 6
mutadores usados — plausível, não confirmado — não é isso que explica o
binário final vazio, já que os `.o` principais como `server.o`/`response.o`
continuam a ter a secção mesmo quando o binário final não tem).

**Não root-causado.** O comportamento não é determinístico da forma simples
que pensei (`-j1` vs `-j2`); pode ser uma interação mais subtil do
`libtool`/Autotools recursivo do `lighttpd` com a secção customizada do
`mull`, possivelmente dependente de ordem de link ou de alguma
característica dos ficheiros que faltam. Fica registado como problema em
aberto, sem solução aplicada.

**Este é o segundo problema real e não resolvido encontrado só no
`lighttpd`** (o primeiro foi a regressão do `p2`). Ao contrário do `gzip`
(onde cada problema teve uma causa isolada e uma correção definitiva em
poucas iterações), o `lighttpd` está a mostrar-se uma família
estruturalmente mais difícil de instrumentar de forma fiável — não é claro
que valha a pena continuar a investir tempo de depuração de baixo nível
aqui sem uma decisão explícita sobre prioridade, dado que `libtiff` e
`python` ainda nem começaram a ser investigados.

#### Investigação `libtiff` (2026-07-31) — dois problemas reais, ambos com causa isolada e corrigida

Spike real, mesmo padrão das anteriores. Achados:

- Imagem original também é **Ubuntu 14.04 (Trusty)**. `CFLAGS` original
  **tem `-m32`** (`-m32 -Wall -W`) — como o `gzip`, não como o `lighttpd`.
- Sistema de build: Autotools. Testes seguem o **mesmo padrão do `gzip`**
  (`test.sh` → `perl libtiff-run-tests.pl <N>` → `` `make $name` `` —
  confirmado por leitura direta do `.pl`), não o padrão Perl-TAP do
  `lighttpd`. Isto é o padrão dominante nas famílias ManyBugs investigadas
  até agora (`gzip` e `libtiff`); `lighttpd` é a exceção.

**Problema 1 — `configure` falha sem `g++`:** libtiff testa suporte a C++
opcional durante `./configure`; falha com "C++ preprocessor fails sanity
check" se `g++`/`g++-multilib` não estiverem instalados. Resolvido
adicionando ao Dockerfile.

**Problema 2 — `test/Makefile` tem uma substituição Automake por
resolver:** `make` falha com `Makefile:978: *** missing separator` nas
linhas `@am__EXEEXT_TRUE@.test$(EXEEXT).log:` /
`@am__EXEEXT_TRUE@\t@p='$<'; ...` — o `config.status` gerado pelo
`autoconf`/`automake` modernos do bionic não substitui esta condicional
corretamente (o checkout é de 2005, com Automake muito mais antigo).
`autoreconf -fi` (regenerar tudo do zero) **falha** por um conflito
`AC_CONFIG_MACRO_DIRS` vs. `ACLOCAL_AMFLAGS` — não vale a pena perseguir,
é um problema mais fundo de incompatibilidade de versões de autotools.
**Correção aplicada, cirúrgica**: `sed -i "s/^@am__EXEEXT_TRUE@//"
test/Makefile` depois de `./configure` (que regenera este ficheiro a cada
run — o patch tem de vir depois, não antes). Resolve porque a regra de
`pattern rule` afetada não é usada pelos alvos `.sh.log` explícitos que os
testes do catálogo realmente invocam.

**Problema 3 — CFLAGS têm de ser gravados no `configure`, não só passados
ao `make` inicial (ao contrário do `gzip`):** os testes do `libtiff`
compilam os seus próprios executáveis pequenos **sob demanda**
(`short_tag.c`→`short_tag`, etc., disparado pelo próprio `` `make
$name` `` dentro de `libtiff-run-tests.pl`) — e essa compilação ad-hoc,
posterior ao build inicial, **não herda o override de `CFLAGS` passado à
invocação de `make` original**; usa o `CFLAGS` congelado no `Makefile`
pelo `configure`. Com `configure` chamado sem `CFLAGS` (como fizemos para
o `gzip`), essa compilação tardia sai **64-bit por omissão**, e o link
falha (`i386 architecture ... incompatible with i386:x86-64 output`)
contra a `libtiff.a` já construída em 32-bit. **Correção:** gravar os
flags completos do `mull` (incluindo `-m32`) diretamente em `./configure
CFLAGS="..."`, não confiar só no `make CFLAGS=...` do build inicial —
inverte a lição do `gzip` (lá, gravar no `configure` causava o CFLAGS do
`Makefile` ganhar ao `make CFLAGS=...` posterior; aqui, é precisamente
isso que se quer, porque há uma SEGUNDA invocação de `make` sem override
explícito que precisa do mesmo resultado). **Ambas as lições ficam
válidas, dependendo se o projeto compila algo sob demanda durante os
testes** (`libtiff`: sim; `gzip`: não, o binário é único e já está
pronto antes dos testes correrem).

Depois destas três correções: build 100% limpo, `.mull_mutants` embutido
em `tools/tiffcp` (usado pelos testes), e gate de equivalência (`n1`/`p1`
via `test.sh`, cwd `/experiment`) bate exatamente com o binário original
(`n1` FAIL em ambos, `p1` PASS em ambos).

**Fase 1 concluída para `libtiff` (2026-07-31).** As três correções acima
foram generalizadas em `prepare_mull_checkout.py`
(`CFLAGS_AT_CONFIGURE_TIME`, `POST_CONFIGURE_PATCH`, por projeto — e
corrigido de caminho um bug de `binary_relpath` que só funcionava por
acaso para `gzip`/`lighttpd`, truncando sempre para o nome do ficheiro em
vez do caminho relativo completo, o que já estava errado para `libtiff`
com `tools/tiffcp`) e em
[`Dockerfile.libtiff`](scripts/manybugs/mull/Dockerfile.libtiff)
(`gcc-multilib`+`g++`+`g++-multilib`). Rodado o script real (não só
manualmente) contra o alvo piloto: `build_ok: true`,
`mutants_embedded: true`, `binary_path` correto
(`/experiment/src/tools/tiffcp`), gate de equivalência confirmado no
container gerado pelo script (`n1` FAIL, `p1` PASS, iguais ao original).

**Ainda não validado:** execução real do `mull-runner` contra um alvo do
catálogo `libtiff` (`tif_dirread.c`/`TIFFReadDirectory`) — ao contrário do
`gzip`/`lighttpd` onde há **um** binário fixo por trás de todos os testes,
o `libtiff` tem **múltiplos executáveis pequenos** (`long_tag`,
`short_tag`, `tiffcp`, etc.) que linkam estaticamente a mesma biblioteca
mutada — não verificado ainda como isso interage com o modelo do
`mull-runner` (que espera um binário posicional para descoberta de
mutantes) nem se o bug `test.sh`/`make` (confirmado para `gzip`) também
afeta aqui. Não decidido ainda se compensa investir nisso antes de fechar
`libtiff`.

**Ainda em aberto (não bloqueia Fase 2 para `gzip`, mas bloqueia expandir
para outras famílias):**
- `Dockerfile.lighttpd` (parcial — 2 problemas reais não resolvidos, ver
  acima) / `Dockerfile.libtiff` (compilação e gate de equivalência
  resolvidos; execução real do `mull-runner` ainda não validada) /
  `Dockerfile.python`/`Dockerfile.gmp` — nenhum destes dois últimos
  investigado ainda. Cada família precisa da própria investigação de
  glibc/arquitetura (não assumir bionic+`-m32`; `python` provavelmente é
  64-bit e pode tolerar glibc mais novo, `gmp` tem assembly inline por
  arquitetura e é o candidato mais arriscado — se não compilar sob Clang
  de forma viável, marcar **fora de escopo** em vez de bloquear as outras
  famílias).
- Confirmar se o bug do wrapper `test.sh`/`make` (Fase 2/Etapa D,
  confirmado para `gzip`) também ocorre em `libtiff` (mesmo padrão de
  teste, suspeita forte que sim) antes de generalizar
  `run_mull_subject.py` além de `gzip`.

**Critério de saída (gzip): atingido.** Para as demais famílias: uma
imagem que builda com sucesso via Clang, produz um binário com
`.mull_mutants` embutido, e passa o gate de equivalência de runtime da
família (arquitetura/flags a determinar por família, não copiado de
`gzip` sem verificar).

### Fase 2 — Execução do `mull` por alvo (scripts)

**Meta:** rodar o `mull` escopado a um alvo específico do catálogo
(arquivo + intervalo de linhas do `target_tests.csv`), análogo ao que
`run_major_subject.py` faz para Major.

**Status: concluída para `gzip` (2026-07-30).**
[`scripts/manybugs/mull/run_mull_subject.py`](scripts/manybugs/mull/run_mull_subject.py)
implementado e validado end-to-end contra o alvo piloto real, com um único
comando (`--subject-id gzip-2009-08-16-... --target-id
gzip_2009_08_16_huft_build__line292_495 --target-file inflate.c
--start-line 292 --end-line 495 --catalog-file
harness/datasets/catalogs/manybugs_gzip_pilot.json`): resolve os testes
elegíveis via `target_tests_for()` (reaproveitado do harness, não
reinventado), chama `prepare_mull_checkout.py` (Fase 1), resolve
`test_id → nome do script Autotools` parseando `test.sh` +
`*-run-tests.pl` (mesmo padrão de `ManyBugsAdapter._discover_test_ids`), e
roda `mull-runner-9` uma vez por `test_id` com um wrapper de invocação
direta (contornando o bug `test.sh`/`make` documentado acima). Resultado
agregado por `mutant_id` bateu exatamente com a validação manual: **44
killed / 24 survived** no intervalo do alvo. Salva um `.sqlite` bruto por
`test_id` em `<report-dir>/<target_id>__<test_id>.sqlite`, pronto para a
Fase 3 normalizar/agregar — a agregação em si (a query
`ATTACH DATABASE`/`JOIN` por `mutant_id` usada para validar o resultado)
ainda não está encapsulada em código de produção, fica para
`normalize_mull_report.py` (Fase 3, próximo passo).

Só validado para `gzip` — a resolução de `test_id`→script assume a
convenção `test.sh`/`*-run-tests.pl`/`@tests` deste projeto; confirmar que
as outras famílias seguem o mesmo padrão antes de reaproveitar sem
adaptar.

**Correção em relação à suposição inicial:** o CLI do `mull-runner-9`
(verificado via `--help-hidden` no PoC) não é um `mull.yml` monolítico como
eu tinha assumido — é orientado a flags: `mull-runner-9 [opções] <input
file> [free-form-arguments]`, com `--test-program=<path>`,
`--reporters=SQLite,IDE,...`, `--timeout=<ms>`, `--workers=<n>`,
`--report-dir=<dir>`. O modelo de execução real do `mull` é: o binário é
compilado com `-fpass-plugin=mull-ir-frontend-N` (não uma ferramenta
separada de instrumentação — ver "Fluxo real do `mull` validado
ponta-a-ponta"), todos os mutantes ficam embutidos atrás de flags
condicionais, e `mull-runner` re-executa esse binário uma vez por mutante,
delegando o julgamento de "passou/falhou" a `--test-program` quando o
programa sob mutação não é ele mesmo um executável de testes (exatamente o
caso do `gzip`/ManyBugs: quem decide pass/fail é o `test.sh`/Perl externo,
não o binário mutado diretamente).

**Isso já foi validado ponta-a-ponta com um mutante sintético** (ver seção
acima). O que falta validar é a aplicação ao alvo real, e isso está agora
bloqueado pelo gate de equivalência de runtime (Fase 0) — não pelo mecanismo
do `mull` em si, que está resolvido.

**Correção quanto à granularidade por teste (não implementada no PoC, mas
necessária):** um único `--test-program` que roda `n1`+`p1`+`p2` numa
invocação só devolve ao `mull` um resultado binário agregado
(pass/fail do wrapper inteiro) — perde-se qual teste especificamente matou
o mutante, o que impede reconstruir uma kill matrix por teste equivalente à
de `test_results.csv`/`kill_matrix_long.csv` do Major. Para preservar essa
granularidade, `run_mull_subject.py` precisa rodar uma campanha `mull`
**separada por `test_id` elegível**, e agregar os resultados depois pelo
identificador estável do mutante (mutador + arquivo + linha + coluna, que o
próprio `mull` já usa como `mutant_id` no SQLite — confirmado idêntico entre
execuções diferentes no PoC). O `mull` documenta suporte a acumular
resultados de múltiplos test targets num único relatório
(ver [Multiple Test Targets](https://mull.readthedocs.io/en/latest/tutorials/MultipleTestTargets.html)).

**Achado crítico validado no PoC (2026-07-30): o `--test-program` NÃO pode
chamar `test.sh <test_id> dummy`.** Essa era a suposição original desta
seção, mas correr assim faz o `mull-runner` reportar **100% dos mutantes
como Survived, para os três testes (`n1`, `p1`, `p2`), inclusive mutações
óbvias em `huft_build`** — mutation score 0% consistente demais para ser
real (confirmado que não é um resultado genuíno: um wrapper que sempre
retorna `exit 1` corretamente produz 100% killed, então o mecanismo
`mull-runner`↔`--test-program` funciona; o problema é específico à cadeia
`test.sh`→`gzip-run-tests.pl`→`make <test>.log`). Isolado experimentalmente:
invocar o script de teste do Autotools **diretamente** (`bash
tests/hufts`, `bash tests/helin-segv`, `bash tests/memcpy-abuse`), saltando
`test.sh`/`gzip-run-tests.pl`/`make`, produz resultados corretos e variáveis
por mutante (`n1`: 13 killed/54 survived no intervalo do alvo; `p1`
(`helin-segv`): 0/68, esperado — não cobre `huft_build`; `p2`
(`memcpy-abuse`): 44/22; agregado por `mutant_id`: **44 killed / 68
survived** no intervalo `292-495`). Causa raiz não totalmente diagnosticada
(candidato mais provável: o `Makefile` gerado pelo Autotools/Automake tem um
comentário próprio sobre "Save and restore TERM around use of
TESTS_ENVIRONMENT", sugerindo que a camada `make <test>.log` manipula/limpa
variáveis de ambiente antes de invocar o script de teste — o que apagaria a
variável de ambiente interna que o `mull-runner` usa para sinalizar ao
binário instrumentado qual mutante está ativo). Não investigado ao ponto de
confirmar a variável exata; não é necessário para contornar o problema.

**Correção para `run_mull_subject.py`:** o wrapper de `--test-program` deve
invocar o script de teste do projeto diretamente (equivalente a `bash
tests/<nome-do-script>`), replicando a lógica mínima de preparação que
`test.sh`/`gzip-run-tests.pl` faziam antes de invocar o script (limpar
logs/artefactos residuais do teste anterior, `cd` para o diretório de
testes) — **sem** passar pela camada `make <test>.log`/Automake. Isso é
específico ao `gzip`; para as outras famílias (Fase 1), confirmar se o
mesmo problema ocorre e qual é o script de teste "cru" equivalente por
família antes de assumir que o padrão se replica sem checar.

- `scripts/run_mull_subject.py`:
  - recebe `--subject` (scenario ManyBugs), `--target-file`,
    `--start-line`/`--end-line` (do catálogo)
  - reaproveita `prepare_mull_checkout.py` para obter o build instrumentado
    (`clang-N -O1 -fexperimental-new-pass-manager -fpass-plugin=...`,
    `CFLAGS`/`LDFLAGS` passados como argumento de `make`, não só env var —
    ver achado de engenharia acima)
  - para cada `test_id` elegível do alvo (`target_tests_for()`): gera um
    `--test-program` wrapper que invoca **diretamente** o script de teste
    correspondente (não `test.sh`/`make <test>.log` — ver achado crítico
    acima) e propaga o exit code sem alteração; roda `mull-runner-N` uma vez
    por `test_id`
  - `--mutators` — conjunto default de mull para C (aritméticos,
    relacionais, condicionais, remoção de chamada, etc.) — registrar a
    lista escolhida explicitamente para reprodutibilidade e para poder
    comparar depois com os operadores do Major
  - `--timeout` alinhado a `ManyBugsAdapter.TEST_TIMEOUT` (120s)
  - `--reporters=SQLite,IDE`
  - salva relatório bruto por `test_id` em
    `harness/executions/c/mull/execution/<run_name>/raw/<target_id>__<test_id>.sqlite`,
    para agregação na Fase 3
- **Escopo por linha:** o `mull` mutará todo o arquivo compilado (unidade de
  compilação), não só a função-alvo. Como os catálogos ManyBugs já definem
  `start_line`/`end_line` por alvo, um passo de pós-filtragem é obrigatório:
  descartar do relatório qualquer `MutationPoint` cuja linha caia fora de
  `[start_line, end_line]` antes de normalizar. Isso replica o que o
  `generate_major_dsl.py`/`normalize_major_mml.py` fazem para escopar o
  Major aos métodos do catálogo, só que como filtro pós-execução em vez de
  DSL pré-execução (o `mull` não tem granularidade de método/linha na config
  de forma tão direta quanto o MML do Major).

**Critério de saída: atingido para `gzip`**, com uma correção ao desenho
original — `run_mull_subject.py` (implementado) **não** filtra por
`start_line`/`end_line` antes de salvar; salva o `.sqlite` bruto completo
por `test_id` (todos os mutantes do arquivo) e deixa o filtro de linha para
a normalização (Fase 3, ainda por escrever), já que o SQLite do `mull`
suporta a query de filtro diretamente (`WHERE filename LIKE ... AND
line_number BETWEEN ...`, validado manualmente) sem precisar reprocessar o
relatório bruto antes. Mantém o resultado equivalente, só move o passo de
filtragem de script para script.

### Fase 3 — Normalização para o schema do harness

**Meta:** converter o relatório do `mull` para os mesmos formatos tabulares
usados pelo Major, para que os scripts de comparação existentes (ou seus
equivalentes C) funcionem sem reescrever a lógica de análise.

**Status: concluída para `gzip` (2026-07-30).**
[`scripts/manybugs/mull/normalize_mull_report.py`](scripts/manybugs/mull/normalize_mull_report.py)
implementado e validado end-to-end contra os relatórios reais da Fase 2:
lê os SQLite brutos por `test_id`, filtra por `[start_line, end_line]` via
SQL (`WHERE filename LIKE ... AND line_number BETWEEN ...`), agrega pelo
`mutant_id` (identificador nativo do `mull`, já confirmado estável entre
execuções — mutador+arquivo+linha+coluna), e escreve
`results.csv`/`test_results.csv` **reaproveitando diretamente**
`harness.models.MutantResult`/`TestObservation` e os helpers existentes
`append_result_csv`/`append_test_results_csv`/`summarize_results_csv` — zero
schema novo, zero duplicação da lógica de dedup/sumarização.

Confirmado por leitura do código-fonte do `mull` 0.18.0
(`include/mull/ExecutionResult.h`, não documentado nas páginas renderizadas)
que a coluna `status` do SQLite é um enum inteiro:
`0=Invalid 1=Failed 2=Passed 3=Timedout 4=Crashed 5=AbnormalExit 6=DryRun
7=FailFast 8=NotCovered`. `Failed`/`Timedout`/`Crashed`/`AbnormalExit` contam
como esse teste ter matado o mutante (timeout conta como kill, mantendo
consistência com o Major); `NotCovered` não é "sobreviveu" — é "este teste
específico não cobriu o mutante", e não deve ser confundido com survived
quando agregado com outros testes que podem tê-lo coberto e matado.

**Desvio em relação ao desenho original desta seção:** em vez de introduzir
três colunas novas (`qualification_status`/`coverage_status`/
`execution_status`), a implementação real usa os campos já existentes de
`TestObservation` (`outcome` ∈ {PASS,FAIL,NOT_RUN}, `failure_type` com o
nome do status bruto do `mull` quando `outcome=FAIL`, `message` com o status
bruto completo + mutador + linha:coluna) — preserva a mesma informação
(estado bruto do `mull` por teste, não colapsado cedo demais) sem estender
os dataclasses partilhados do harness. `qualification_status` não virou uma
coluna por linha: continua a ser um *gate* upstream, resolvido em
`prepare_mull_checkout.py` (`build_ok`/`mutants_embedded`) antes da Fase 2
sequer correr — mais parecido com `BASELINE_FAIL` do `MutantEvaluator`
(checado uma vez, não por mutante) do que uma dimensão por linha.

**Validado:** rodar contra os relatórios reais do alvo piloto produz 68
mutantes em `results.csv`, `mutation_score: 0.676` (46 killed / 22
survived) — mais preciso que a validação manual anterior (44/24), porque
esta implementação conta corretamente `Timedout` como kill, o que a query
SQL manual ad-hoc não fazia.

**`compute_mull_baseline_analysis.py` implementado e concluído
(2026-07-31).**
[`scripts/manybugs/mull/compute_mull_baseline_analysis.py`](scripts/manybugs/mull/compute_mull_baseline_analysis.py)
— mirror de `compute_major_baseline_analysis.py`, mesmo algoritmo de
DMR/kill-matrix/dominância/indistinguibilidade, adaptado só na leitura de
entrada: como `run_mull_catalog.py` escreve um `results.csv`/
`test_results.csv` **por alvo** (não um único combinado como o Major), este
script faz glob+concat pelos subdiretórios de `<run-root>` antes de aplicar
o mesmo algoritmo linha a linha. Rodado contra os resultados reais
persistidos do catálogo piloto:
- 180 mutantes executáveis (68+8+39+39+26, bate exatamente com a Fase 4)
- DMR = 0% (esperado — `mutant_id` do `mull` já é o identificador de
  conteúdo estável, sem duplicação por design; a coluna existe só por
  paridade de schema com o Major, não porque se espere DMR>0 aqui)
- 158 killed (46+112, bate exatamente com os scores por alvo já
  registados na Fase 4) / 24 dominadores / 1671 pares indistinguíveis
- Escritos em `harness/executions/c/mull/{mull_dmr_detail,
  kill_matrix_long_mull,mutant_summary_mull}.csv`

**Ainda não implementado** (não bloqueia comparação básica com LLM):
- `mull_root()` em `harness/storage/layout.py` — por agora os scripts
  recebem `--run-dir`/`--run-root`/`--out-dir` explícitos em vez de
  resolver o caminho sozinhos. Adicionar quando houver mais de um `run_name`
  concorrente e a resolução manual do caminho começar a doer.

**Critério de saída:** atingido por completo — `results.csv`/
`test_results.csv` (mesmo shape de colunas dos equivalentes Java,
reaproveitado diretamente) e `mutant_summary_mull.csv`/
`kill_matrix_long_mull.csv` (mesmo shape dos equivalentes `_major`), ambos
gerados a partir de dados reais do catálogo piloto completo.

### Fase 4 — Driver em lote sobre o catálogo

**Meta:** rodar o baseline `mull` para todos os alvos de um catálogo C
piloto (começar por `manybugs_gzip_pilot.json`, depois estender).

**Status: concluída para `manybugs_gzip_pilot` (2026-07-30).**
[`scripts/manybugs/mull/run_mull_catalog.py`](scripts/manybugs/mull/run_mull_catalog.py)
implementado: itera os `targets` do catálogo, resolve testes elegíveis via
`target_tests_for()`/`load_target_test_map()` (mesmo mecanismo que o
`MutationRunner` já usa para LLM — garante oráculo idêntico entre baseline
`mull` e mutantes LLM), chama `run_mull_subject`+`normalize_mull_report`
por alvo (importados como funções, não subprocess), captura exceções por
alvo sem abortar o catálogo inteiro (um alvo com erro fica `status:
ERROR`, os outros continuam).

**Rodado contra os 5 alvos reais do catálogo — resultados persistidos em**
[`harness/executions/c/mull/execution/mull_gzip_pilot_full/`](harness/executions/c/mull/execution/mull_gzip_pilot_full/):

| target_id | mutantes | mutation score |
|---|---|---|
| `gzip_2009_08_16_huft_build__line292_495` | 68 | 0.68 |
| `gzip_2009_09_26_treat_stdin__line606_692` | 8 | 1.00 |
| `gzip_2009_10_09_get_method__line1251_1436` | 39 | 1.00 |
| `gzip_2010_01_30_get_method__line1237_1427` | 39 | 1.00 |
| `gzip_2010_02_19_main__line407_584` | 26 | 1.00 |

5/5 alvos completaram OK. Os 4 alvos com score 1.00 foram verificados
manualmente (não aceites às cegas) — `test_results.csv` mostra resultado
**variado** por teste (`n1` mata todos os mutantes checados, `p1`/`p2`/`p3`
sobrevivem consistentemente), o padrão esperado de um teste-oráculo
estreito e específico à função, não um artefacto de wrapper quebrado (que
seria "tudo falha, sempre, para todos os testes" — já visto e descartado
antes).

**Dois problemas reais encontrados e corrigidos durante esta corrida, não
hipotéticos:**
1. **4 dos 5 alvos falharam a build** na primeira tentativa
   (`make: *** [gzip.info] Error 127`, `makeinfo: command not found`) —
   só o alvo piloto (`2009-08-16`) não gera documentação Texinfo no build.
   Corrigido adicionando `texinfo` ao `Dockerfile.gzip`.
2. **Containers órfãos**: quando `prepare_mull_checkout` retorna
   `build_ok=False` (alvo não qualificado), `run_mull_subject` levantava
   `RuntimeError` sem remover o container — 6 containers ficaram vivos
   depois da corrida com falha. Corrigido: remoção do container tanto no
   caminho de "não qualificado" quanto num `finally` a envolver o loop de
   testes, para qualquer exceção durante a Fase 2 sempre limpar o
   container.

**Critério de saída: atingido.** Catálogo `manybugs_gzip_pilot` completo,
baseline `mull` executado, resultados persistidos em
`harness/executions/c/mull/execution/`.

### Fase 5 — Comparação com mutantes LLM

**Meta:** plugar o baseline `mull` na mesma análise de fault coupling /
trivialidade já usada para comparar Major vs LLM em Java.

**BLOQUEADO (2026-07-31) — pré-requisito fora do âmbito deste plano, não é
mais uma questão de "escrever o script".** `compute_fault_coupling_major.py`
depende de ficheiros específicos do pipeline Java que **não têm equivalente
para C ainda**, nem para os mutantes LLM (não é um problema introduzido
pelo `mull`):
- `harness/executions/java/llm/defects4j_triggering_tests.csv` — não existe
  versão C. O conceito mais próximo em C é a coluna `match_mode=oracle` do
  `target_tests.csv` (o teste `n1` de cada alvo), mas ninguém ainda
  escreveu o código que traduz isso para o formato que
  `compute_fault_coupling_major.py` espera.
- `harness/executions/java/llm/fault_coupling_results.csv` (resultados de
  fault coupling já computados para os mutantes LLM) — **não existe
  equivalente em `harness/executions/c/llm/`**. Confirmado por busca no
  repositório: nenhum script escreve isto para C hoje.
- `experiment_index_evaluable.csv` — idem, específico do pipeline Java.

Ou seja: escrever `compute_fault_coupling_mull.py` como "mirror" pressupõe
que a análise de fault coupling **já existe do lado do LLM/C**, e não
existe. Construir essa infraestrutura (decidir o que conta como "triggering
test" em C, calcular fault coupling dos mutantes LLM em si, etc.) é um
projeto à parte, maior que este plano do `mull`, e não deve ser decidido
nem iniciado unilateralmente aqui — é uma escolha de escopo para o
utilizador.

**Quando essa infraestrutura existir**, a parte específica do `mull` nesta
fase continua válida como desenhada:
- `scripts/manybugs/mull/compute_fault_coupling_mull.py` (mirror de
  `compute_fault_coupling_major.py`), produzindo
  `fault_coupling_results_with_mull.csv` no diretório C equivalente.
- `scripts/manybugs/mull/classify_triviality_mull.py` (mirror de
  `classify_triviality_major.py`), se a trivialidade de mutantes for parte
  da análise pretendida para C também.
- Decidir explicitamente quais operadores/mutadores do `mull` são
  comparáveis aos operadores do Major usados hoje — os conjuntos não são
  idênticos (Major é Java-específico), então a comparação deve reportar o
  conjunto de mutadores do `mull` usado, não assumir equivalência 1:1.

**Critério de saída:** existe uma tabela comparando kill rate/DMR entre
mutantes LLM e mutantes `mull` por alvo, no mesmo formato usado para a
comparação LLM vs Major em Java.

### Fase 6 — Hardening

- Cachear as imagens derivadas (Fase 1) localmente ou em um registry interno
  para não recompilar Clang/mull a cada execução.
- Definir timeout/paralelismo de execução do `mull-runner` (`-workers`) para
  não estourar o tempo de CI/execução em lote.
- Documentar, por família de projeto, quais flags/patches foram necessários
  para compilar sob Clang — isso é conhecimento que vai se perder se não for
  escrito (equivalente ao que `normalize_major_mml.py` documenta para os
  operadores do Major).
- Adicionar smoke test (`scripts/test_manybugs_mull_smoke.py`, mirror de
  `test_defects4j_smoke.py`) que roda o pipeline completo em 1 alvo pequeno.

## Risco: o rebuild em bionic não é comportamentalmente equivalente ao ambiente original

**Este é o achado mais importante da sessão de PoC e eleva o nível de risco
de toda a Fase 1 — mas a formulação abaixo foi revisada após revisão crítica
(2026-07-22) para não afirmar uma causa que não foi isolada.**

Ao tentar rodar o `mull` de verdade contra o alvo `gzip-2009-08-16` (não um
exemplo sintético), o teste de regressão `helin-segv` (um dos testes `p*`
do catálogo, não relacionado à função-alvo `huft_build`, mas parte do mesmo
binário) **segfaulta** quando o `gzip` é recompilado dentro do container
bionic — com Clang e com GCC, com e sem instrumentação do `mull`. No binário
pré-compilado original da imagem `prosyslab/manybugs` (Xenial), o mesmo
teste passa normalmente.

Isolamento feito (4 casos testados de verdade):

| Build | `helin-segv` |
|---|---|
| Binário original da imagem (`prosyslab/manybugs`, Xenial, gcc 5.4) | PASSA |
| Rebuild em bionic com `clang-9 -O2 -g` (sem mull) | **SEGFAULTA** |
| Rebuild em bionic com `clang-9 -O1 -fpass-plugin=mull-ir-frontend` | **SEGFAULTA** |
| Rebuild em bionic com `gcc -O2 -g` (sem Clang, sem mull) | **SEGFAULTA** |

**O que isto exclui razoavelmente:** a instrumentação do `mull` como causa
(D falha igual a C, sem mutantes) e o Clang como causa exclusiva (GCC puro
falha igual). **O que isto NÃO isola:** a comparação principal (linha 1 vs.
linhas 2-4) muda várias variáveis ao mesmo tempo — binário pré-compilado vs.
rebuild, Xenial vs. bionic, GCC 5.4 vs. GCC 7/Clang 9, flags de configuração
e build, possivelmente arquitetura/bitness, estado exato das fontes e
patches, versões de bibliotecas além da glibc, otimizações/undefined
behaviour, working directory e preparação dos testes. Falta o controlo
essencial — reconstruir as mesmas fontes **dentro da imagem Xenial
original**, com o toolchain e flags originais, e correr `helin-segv` aí.
Sem esse caso, não se sabe se o desvio vem da glibc/allocator, do rebuild em
geral, das flags, da arquitetura, ou de diferenças nas fontes.

**Conclusão correta neste momento:** a falha não depende do `mull` e não é
exclusiva do Clang; alguma diferença entre o artefacto original e o rebuild
em bionic altera o comportamento. **A hipótese glibc/allocator é forte
(`helin-segv` testa um bug histórico de segfault, classicamente sensível a
layout de heap), mas ainda não está isolada** — não deve ser tratada como
facto estabelecido no resto deste plano.

**Implicação:** a estratégia da Fase 1 ("builda em bionic, roda o `mull`
lá") resolve o problema de *compilação* mas não garante fidelidade de
*comportamento em runtime* frente ao binário oracle original. É mais sério
que o risco de toolchain original porque não tem mitigação óbvia por flags
de compilador.

### Etapa A — matriz causal mínima (a executar antes de continuar a Fase 1)

Para o alvo piloto, comparar sistematicamente:

| Caso | Build | Runtime |
|---|---|---|
| A | binário fornecido pelo ManyBugs | Xenial original |
| B | rebuild com GCC e receita originais (mesma versão de GCC da imagem, mesmas flags do `configure`/`Makefile` original) | Xenial original |
| C | rebuild com Clang, sem `mull` | mesmo runtime do caso B |
| D | Clang + `mull`, todos os mutantes desativados (build instrumentado, mas sem ativar mutação) | mesmo runtime |
| E | Clang + `mull`, mutantes ativos | mesmo runtime |

Interpretação:
- A passa, B falha → problema de reprodutibilidade do rebuild em si, não da
  glibc do bionic.
- B passa, C falha → diferença de compilador/flags ou undefined behaviour
  exposto por Clang.
- C passa, D falha → a instrumentação do `mull` altera o baseline.
- D passa e E varia de forma consistente com os mutantes → campanha de
  mutação válida.
- B, C, D passam em Xenial mas falham em bionic → aí sim a hipótese de
  runtime (glibc/bionic) ganha evidência forte, isolada das outras
  variáveis.

Comparar também `file`, arquitetura, `readelf -d`, `ldd`, flags gravadas
(`-grecord-command-line`) e hashes das fontes entre os builds, para excluir
divergência de fontes/patches como causa.

**Ordem de execução — começar pelo caso B, isoladamente, antes de qualquer
outra coisa.** O caso B (rebuild com GCC e receita originais, dentro do
Xenial original) é decisivo e barato relativamente às alternativas: se A
passa e B falha, o problema é reprodutibilidade do próprio rebuild
(diferenças de patch/fonte/flags), não a glibc do bionic nem nada
relacionado ao `mull` — e nesse caso não vale a pena investir em Clang/`mull`
compatível com Xenial ou em ligação contra sysroot Xenial (Etapa B abaixo)
antes de primeiro conseguir um rebuild simples e fiel reproduzir o
comportamento original. Só se B passar (rebuild GCC dentro de Xenial bate
com o binário original) faz sentido avançar para C/D com Clang/`mull`,
inicialmente ainda dentro do runtime Xenial se possível.

### Etapa B — priorizar artefactos ligados contra o runtime Xenial original

**Só entra em jogo depois do caso B da matriz (Etapa A) confirmar que o
rebuild GCC dentro do Xenial original reproduz o binário/comportamento de
referência.** Se esse caso B falhar, o problema é reprodutibilidade do
rebuild em si — investir em Clang/`mull` compatível com Xenial ou em
ligação contra sysroot Xenial não resolveria nada, porque a causa não seria
o compilador nem o runtime de destino.

Confirmado o caso B, estratégia técnica preferida, por ordem:

1. **Clang/`mull` executáveis no próprio Xenial** — melhor fidelidade, se
   for possível construir ou instalar versões compatíveis com glibc 2.23
   (não tentado ainda; o PoC descartou Xenial só porque o `apt` da imagem
   `prosyslab/manybugs` está morto/EOL, o que é diferente de "impossível
   instalar Clang/mull ali por outros meios", ex. binários estáticos).
2. **Clang 9 em bionic usando headers/bibliotecas/sysroot de Xenial** — o
   compilador/plugin corre em bionic, mas o artefacto é ligado contra
   Xenial; testado depois no container original.
3. **Construir Clang/`mull` a partir de source sobre Xenial** — mais caro,
   mas cientificamente mais limpo que mudar o runtime.
4. **bionic como está hoje no PoC** — último recurso, sujeito ao gate de
   equivalência por alvo (Etapa C) em vez de assumido como equivalente.

Não misturar loaders/libc/bibliotecas de distribuições diferentes via
`chroot` parcial ou só `LD_LIBRARY_PATH` — tende a criar um ambiente ainda
menos controlado que qualquer uma das opções acima.

### Etapa C — gate de equivalência por alvo (obrigatório antes de qualquer campanha `mull`)

Antes de gerar mutantes para um alvo, o binário instrumentado com mutantes
desativados (caso D da matriz) deve, contra o mesmo runtime candidato:
- compilar e iniciar;
- passar exatamente os testes elegíveis do alvo (`target_tests_for()`);
- produzir os mesmos outputs/oráculos do build de referência (caso A/B);
- repetir o resultado (idealmente 3x, para excluir flakiness);
- não introduzir crashes/timeouts novos em relação ao caso A.

Se falhar, o alvo fica marcado `UNQUALIFIED_RUNTIME` — não `BASELINE_FAIL`
(que é uma categoria do `MutantEvaluator` para outra coisa: build/teste do
próprio mutante) nem um resultado `mull` válido. A qualificação é por alvo;
a imagem/toolchain pode ser partilhada por família se o caso A/B/C/D bater
para os alvos dessa família.

### Recomendação (histórica — ver "RESOLVIDO" abaixo para o resultado real)

Não tratar bionic "caso a caso" como estratégia principal sem o gate acima
— isso enfraquece a comparabilidade com os mutantes LLM, que rodam no
ambiente ManyBugs original via `ManyBugsAdapter`. Preferir investigar
primeiro um binário `mull` ligado contra Xenial (Etapa B, opções 1-2);
introduzir o gate de equivalência por alvo (Etapa C) antes de qualquer
campanha real; e, só se isso se revelar tecnicamente inviável, usar bionic
como baseline secundária, explicitamente rotulada como "rehosted" nos
resultados publicados — não comparada diretamente com os resultados LLM
como se os ambientes fossem equivalentes.

### RESOLVIDO (2026-07-22): causa isolada — não é glibc, é bitness (32 vs 64-bit)

Executado o caso B da matriz (Etapa A), como acordado: rebuild com GCC e a
receita original, dentro do próprio container Xenial da imagem
`prosyslab/manybugs`. Achado decisivo antes até de compilar:
`config.log`/`Makefile` do `gzip` original mostram `CC=gcc CFLAGS=-m32
LDFLAGS=-m32 CXXFLAGS=-m32` — o binário oracle é **ELF32/i386**
(confirmado via `readelf -h`), não 64-bit. O meu PoC anterior nunca tinha
passado `-m32` em nenhum dos 4 builds testados — todos compilaram 64-bit
por omissão. Ou seja, a comparação original não era "Xenial vs. bionic",
era "32-bit vs. 64-bit" disfarçada de comparação de ambiente.

**Caso B, com `-m32`, dentro do Xenial original:** `make clean && ./configure
CFLAGS=-m32 LDFLAGS=-m32 CXXFLAGS=-m32 && make` reproduziu o binário
original **bit-a-bit** (`md5sum` idêntico). Reprodutibilidade total
confirmada — não há problema de fontes/patches/flags divergentes.

**Teste decisivo (isolando arquitetura de ambiente):** repeti o rebuild em
**bionic** (não Xenial), desta vez com `-m32`, com GCC 7.5 e depois com
Clang-9 — `helin-segv` **passa** em ambos, via `test.sh` oficial. Ou seja,
bionic em si não é a causa; a causa isolada é ter compilado 64-bit em vez
de 32-bit no PoC anterior. Isso fecha a Etapa A: **a hipótese
glibc/allocator estava errada**; a variável real era arquitetura/bitness,
que já estava listada como confundida na matriz causal mas nunca tinha sido
isoladamente testada até agora.

**Implicação prática:** as Etapas B (Xenial-sysroot/Clang-em-Xenial) e C
(gate de equivalência formal, multi-execução) descritas acima deixam de ser
necessárias como próximo passo — **bionic + `-m32` já qualifica** para este
alvo piloto, validado abaixo com um teste de equivalência real (não só
`helin-segv`, mas os três testes elegíveis do catálogo).

### Validação completa contra o alvo real (`gzip`/`huft_build`), bionic + `-m32` + `mull`

1. **Gate de equivalência** (bionic, `-m32`, build instrumentado com `mull`
   ativo): `test.sh n1/p1/p2 dummy` — os três **passam**, batendo com o
   baseline real já confirmado em execuções LLM persistidas
   (`baseline_failures: 0`). Não é uma simulação do gate formal da Etapa C
   — é o resultado real desse gate para este alvo.
2. **Achado de engenharia (não relacionado a `mull` nem a bitness):**
   `./configure CFLAGS=-m32 ...` grava `CFLAGS` no `Makefile` gerado; uma
   invocação seguinte `CFLAGS="<flags-mull>" make` (variável de ambiente)
   é **ignorada**, porque a atribuição no `Makefile` tem precedência sobre
   variáveis de ambiente herdadas (regra padrão do GNU Make, sem `-e`). Isso
   fez o primeiro rebuild instrumentado (silenciosamente) compilar sem os
   flags do `mull`, gerando um binário sem nenhuma seção `.mull_mutants` e
   um relatório "No mutants found" sem erro nenhum — mais uma falha
   silenciosa a somar à do `-O1`/`-fexperimental-new-pass-manager`
   documentada acima. **Correção: `run_mull_subject.py` deve passar
   `CFLAGS`/`LDFLAGS` como argumento de linha de comando do `make`
   (`make CFLAGS="..."`), nunca só como variável de ambiente**, quando o
   `configure` já gravou um `CFLAGS` próprio no `Makefile`.
3. **Execução real do `mull-runner-9`** contra o binário completo, com
   `--test-program` chamando `test.sh n1 dummy`: relatório real, mutantes
   reais embutidos em `inflate.c` (símbolo `mull_huft_build_original`
   confirmado no binário final), 68 mutantes no intervalo do catálogo
   (`start_line=292, end_line=495`). **O resultado inicial (todos os 68
   `Survived`, mutation score 0% inclusive nos outros três testes) estava
   errado — não por causa do alvo, mas por um bug real de invocação,
   corrigido e documentado na seção "Achado crítico" da Fase 2/Etapa D
   abaixo.** Depois de corrigido (invocar os scripts de teste diretamente,
   não via `test.sh`/`make`), o resultado real agregado
   (`n1`+`p1`+`p2`, cada um rodado separadamente e agregado por
   `mutant_id`) é **44 killed / 68 survived** no intervalo do alvo — um
   kill rate de ~65%, plausível para código já corrigido (`f`) sendo
   mutado. Ver Fase 2 para o detalhe completo (por-teste: `n1` 13/54,
   `p1`/`helin-segv` 0/68 — esperado, não cobre `huft_build`, `p2`/
   `memcpy-abuse` 44/22).

**Conclusão para a Fase 0:** critério de saída atingido para o alvo piloto.
O item "ainda em aberto" que bloqueava a Fase 0 (gate de equivalência de
runtime) está resolvido para `gzip-2009-08-16-...`/`huft_build`, com
ambiente qualificado = **bionic + `-m32`**. Falta generalizar/revalidar
por família (Fase 1) — `-m32` pode não se aplicar a todas (`python`,
`libtiff`, `lighttpd`, `gmp` podem ter sido buildadas 64-bit originalmente;
isto precisa de ser conferido por família, não assumido).

## Nota (corrigida 2026-07-22): oráculo `n1` — a afirmação original estava errada

A versão anterior deste plano afirmava que `n1` já falha no binário
original e que, por isso, `BaselineEvaluator` já deveria estar a dar
`BASELINE_FAIL` para este alvo em execuções LLM existentes. **Isso foi
verificado e está errado.** Em execuções reais já persistidas
(`harness/executions/c/llm/batch0{2..6}/.../gzip_2009_08_16_huft_build__line292_495/execution/test_results.csv`),
`n1` aparece com `outcome=PASS` para mutantes específicos (ex. `m07`, `m08`
do batch06, ~150ms de duração) — isso demonstra o comportamento observado
nesses mutantes, não o baseline em si (o baseline não é um mutante, é um
build/run separado avaliado antes de qualquer mutante). A evidência direta
de que o baseline partilhado (testes elegíveis contra o código-fonte
`f`, sem mutação) passou é `summary.json` reportar `baseline_failures: 0`
para esses batches, em conjunto com o fluxo do `MutantEvaluator`/
`BaselineEvaluator` (que só chega a avaliar mutantes depois do baseline
passar `build_ok`+`test_ok` para os `eligible_tests`). O `target_tests.csv`
do catálogo confirma `n1/p1/p2` como os três testes elegíveis deste alvo.

A minha invocação manual (`/experiment/test.sh n1 dummy` direto num
container `docker run` novo) divergiu operacionalmente do caminho real do
`ManyBugsAdapter` nalgum detalhe não identificado — candidatos: estado do
container/checkout usado por `checkout_subject`, working directory exato,
ordem de aplicação do fix, binário efetivamente testado, variáveis de
ambiente, ou outro passo de preparação que o adapter faz e a minha
invocação manual pulou. **Não reproduzir esta nota como facto até refazer
o teste seguindo exatamente o caminho do adapter** (idealmente instrumentando
`ManyBugsAdapter._run_single_test` para logar o comando exato, em vez de
reconstruir a invocação manualmente).

## Riscos e Mitigações

| Risco | Mitigação |
|---|---|
| ~~Rebuild em bionic não é comportamentalmente equivalente ao original~~ | **RESOLVIDO (2026-07-22) para `gzip`: causa era bitness (`-m32` da receita original, não usada no PoC inicial), não glibc. Bionic + `-m32` qualifica — ver "RESOLVIDO: causa isolada" acima. Revalidar `-m32`/arquitetura original por família na Fase 1, não assumir 32-bit para todas** |
| Código antigo não compila sob Clang em ambiente moderno (glibc ≥2.28) | PoC por família antes de investir em automação (Fase 0/1); permitir flags permissivas; excluir família se inviável (candidato: `gmp`) — **causa de compilação isolada para `gzip` (símbolos `_IO_*` do gnulib), ver "Achados empíricos"; distinto do risco de runtime acima** |
| `CFLAGS` gravado no `Makefile` pelo `configure` ignora `CFLAGS` passado como variável de ambiente ao `make` seguinte (falha silenciosa, sem erro) | Passar `CFLAGS`/`LDFLAGS` como argumento de linha de comando do `make` (`make CFLAGS=...`) em `run_mull_subject.py`, nunca só como variável de ambiente |
| **`--test-program` chamando `test.sh <id> dummy` (via `make <test>.log`/Automake) faz o `mull-runner` reportar 100% dos mutantes como Survived, mesmo mutações óbvias — resultado silenciosamente errado, não um "0% real"** | **RESOLVIDO (2026-07-30): invocar o script de teste do Autotools diretamente (`bash tests/<script>`), saltando `test.sh`/`gzip-run-tests.pl`/`make`. Causa raiz provável: a camada `make`/Automake limpa/manipula variáveis de ambiente (`TESTS_ENVIRONMENT`), apagando o sinal interno do `mull` para qual mutante está ativo. Confirmar se o mesmo ocorre nas outras famílias antes de generalizar** |
| `mull` mutando o arquivo inteiro em vez do intervalo do alvo | Filtro pós-execução por `start_line`/`end_line` do catálogo (Fase 2) |
| Um único `--test-program` agregando vários `test_id` perde granularidade por teste | Uma execução `mull-runner` por `test_id` elegível, agregada na normalização (Fase 2/3) |
| Build de `python` (interpretador completo) é lento | Rodar por último, medir tempo antes de incluir no catálogo em lote |
| Conjunto de mutadores do `mull` não é diretamente comparável ao do Major | Reportar explicitamente os mutadores usados em vez de assumir paridade (Fase 5) |
| Custo de manter imagens Docker derivadas por família | Centralizar em `scripts/manybugs/mull/Dockerfile.<project>`, versionado no repo, com cache local |
| Reexecutar Clang/LLVM install a cada run é caro | Construir e cachear a imagem uma vez por família (Fase 1), não por execução |
| `-O1`/`-fexperimental-new-pass-manager` obrigatórios para LLVM 9/10, combinação exata não documentada publicamente | Registrado nesta sessão (ver "Fluxo real do mull validado"); não repetir a investigação |

## Não-objetivos (por agora)

- Não integrar o `mull` ao `MutationRunner`/`MutantEvaluator` existentes —
  ele roda como pipeline offline paralelo, igual ao Major.
- Não trocar o `mull` por uma ferramenta de mutação C a nível de source (ex.
  MUSIC, Milu) só para viabilizar um bridge estilo
  `run_major_exported_mutants.py` — decisão registrada na seção "Diferença de
  Operação em Relação ao Major (Java)": o `mull` não exporta source mutado
  (mutação em LLVM bitcode/IR, confirmado na documentação oficial), então
  esse bridge não é possível para ele independentemente de esforço de
  engenharia.
- Não tentar paridade exata de operadores entre `mull` e `Major`.
- Não cobrir `gmp` no primeiro corte, salvo se a Fase 1 mostrar que compila
  sem esforço desproporcional.
- Não alterar o catálogo/`target_tests.csv` existentes — o baseline `mull`
  consome exatamente os mesmos artefatos já usados pelos mutantes LLM.

## Ordem recomendada de execução

1. Fase 0 (spike manual, 1 alvo `gzip`)
2. Fase 1 (Dockerfile reproduzível para `gzip`)
3. Fase 2 + 3 (execução + normalização para o mesmo alvo piloto)
4. Fase 4 restrita ao catálogo `manybugs_gzip_pilot`
5. Só então: repetir Fases 1–4 para `lighttpd`, `libtiff`, `python`
6. Fase 5 (comparação) assim que houver pelo menos um catálogo completo
7. Fase 6 (hardening) e, por último, avaliar `gmp`
