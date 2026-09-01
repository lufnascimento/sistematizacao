# Motor CF0 de família contínua — contrato 1.2

## 1. Decisão de produto

O CF0 é o primeiro estágio calculável depois dos cenários E0. Ele procura uma **família contínua de linhas paralelas no sentido normal**, compatível com o terreno e sem recortes artificiais por zonas topográficas.

Seu objetivo é responder:

> Existe, neste bloco físico de trabalho, um campo de orientação integrável do qual seja possível extrair linhas contínuas, espaçadas, suaves e topologicamente válidas?

O CF0 ainda não responde se a solução constitui curva embutida, terraço de base larga/passante ou ESD dimensionado. Também não autoriza piloto automático, implantação ou responsabilidade técnica. Uma aprovação geométrica sempre permanece `HYDRAULIC_UNCONFIRMED`.

Os três candidatos calculados são preferências diferentes dentro do mesmo motor:

| Candidato | Tendência dominante | Pergunta de comparação |
|---|---|---|
| `CF0A_CONSERVACAO` | acompanhar mais a tangente ao relevo e penalizar o proxy de erosão | quanto de continuidade operacional pode ser preservado sem abandonar a preferência conservacionista? |
| `CF0B_EQUILIBRIO` | equilibrar relevo, comprimento, espaçamento e curvatura | existe uma família intermediária robusta? |
| `CF0C_OPERACAO` | favorecer eixo longo, tiros e menor curvatura | qual é o custo topográfico de priorizar rendimento operacional? |

Eles **não** são sinônimos de `C1_CURVA_EMBUTIDA`, `C2_BASE_LARGA_PASSANTE` e `C3_ESD`. Os produtos C1-C3 usarão famílias CF0 viáveis como geometria de entrada e acrescentarão projeto agronômico e hidráulico.

Na V1, somente os pesos de orientação declarados alteram o campo axial. As métricas resultantes permitem comparação posterior, mas ainda não existe um otimizador multiobjetivo global. Portanto `CF0A`, `CF0B` e `CF0C` são alternativas parametrizadas, não provas de ótimo de Pareto nem “melhor cenário” automático.

## 2. Domínio físico antes da matemática

`field_id` identifica o talhão cadastrado; `work_block_id` identifica o domínio físico no qual uma família pode ser contínua. Os dois conceitos não podem ser confundidos.

Na execução atual, `CONNECTED_COMPONENTS_PER_FIELD` cria um `work_block_id` para cada componente conexo da superfície útil de cada talhão. Isso impede que duas ilhas desconectadas compartilhem uma família apenas porque pertencem ao mesmo cadastro. `component_index` é determinístico e começa em 1.

O contrato 1.2 também particiona de forma auditável todos os componentes reconstruídos. Componentes com área estimada insuficiente para a grade ou largura mínima incompatível com o suporte CF0 entram em `domain_assembly.excluded_components`, com talhão, índice, área, largura mínima rotacionada, contagem estimada de células e códigos de motivo. Os limites efetivos e seus métodos ficam em `work_block_filter`; nenhum valor deve ser inferido pelo relatório. Um componente excluído não vira bloco, não recebe candidato e não é desenhado como sulcação.

Em uma futura execução entre talhões, a divisa cadastral só será dissolvida quando as superfícies operacionais revisadas demonstrarem que ela não é carreador, cabeceira, cerca, vala, rede elétrica, obstáculo ou outra descontinuidade física. Nesse caso, `CROSS_FIELD_DISSOLVED` cria um único domínio físico e a divisa deixa de produzir endpoints.

Se o usuário pedir continuidade entre talhões sem essa evidência, o estágio usa `BLOCKED_CROSS_FIELD_UNCONFIRMED`. Ele não inventa uma passagem.

### Invariante de término

Toda linha publicada deve terminar no contorno físico do `work_block_id` ou em uma barreira física aplicada ao domínio. Uma fronteira analítica, mudança de orientação, célula de baixa coerência ou limite de talhão meramente cadastral nunca autoriza parada, levante ou manobra.

CF0 registra a superfície de cada extremidade, mas não transforma término suportado em manobra autorizada. Autorização de manobra continua dependente de cabeceira, frota e superfície operacional aprovadas.

### Contrato raster e de borda 1.2

O domínio físico vetorial continua sendo a autoridade geométrica. Para evitar perda de suporte em pixels cortados pelo contorno, a máscara de cálculo usa o contrato fixo:

| Campo em `solver_parameters.extraction` | Valor obrigatório | Função |
|---|---|---|
| `solve_mask_rasterization` | `ALL_TOUCHED_SUPERCOVER` | inclui na máscara toda célula tocada pelo bloco físico |
| `contour_extrapolation_method` | `FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED` | estima gradiente local por mínimos quadrados somente para a extração junto à borda |
| `contour_extrapolation_halo_cells` | `1` | limita o suporte extrapolado a uma célula fora da máscara |
| `contour_gradient_lsq_max_radius_cells` | `2` | limita a busca local de vizinhos válidos |
| `contour_gradient_lsq_max_relative_residual` | `0.30` | rejeita estimativa LSQ cujo resíduo relativo exceda o contrato |
| `contour_gradient_lsq_max_condition_number` | `100.0` | rejeita sistema local mal condicionado |
| `contour_gradient_lsq_minimum_neighbor_count` | `3` | exige suporte amostral mínimo |
| `contour_gradient_lsq_required_rank` | `2` | exige estimativa bidimensional identificável |
| `contour_gradient_component_connectivity` | `4` | impede buscar suporte através de outra componente |
| `endpoint_extension_mode` | `TANGENT_ONLY_FAIL_CLOSED` | permite chegar à borda apenas prolongando a tangente da própria linha |

`ALL_TOUCHED_SUPERCOVER` é a máscara em que a fase é resolvida. Ela deve possuir exatamente uma componente 4-conexa: `solve_mask_required_component_count=1` e `solve_mask_component_connectivity=4`. Se a rasterização produzir mais de uma componente, o candidato falha com `WORK_BLOCK_SOLVE_MASK_DISCONNECTED`; o solver não conecta as ilhas nem escolhe uma delas silenciosamente.

O halo de uma célula não amplia esse domínio de solução, não cria área agrícola e não autoriza linha fora do polígono. Ele existe somente como suporte numérico para a interpolação/extrapolação do contorno perto da borda. A estimativa tenta o gradiente disponível no componente e, quando necessário, um ajuste LSQ local. Vizinhança insuficiente, rank menor que 2, resíduo relativo acima de `0.30`, número de condição acima de `100.0` ou componente sem gradiente estimável encerram o caminho com `PHASE_HALO_GRADIENT_UNESTIMABLE`. Depois da extração, toda linha é recortada pela geometria física vetorial.

Também não existe ligação lateral até o ponto mais próximo da borda. Em `TANGENT_ONLY_FAIL_CLOSED`, a extremidade só pode ser prolongada na direção tangente local e encontrar a borda dentro do limite declarado. Se isso não ocorrer, o endpoint falha; o motor não desenha um conector para a `nearest edge`, não projeta a ponta transversalmente e não disfarça a falha topológica.

## 3. Campo axial de orientação

Uma linha agrícola não possui frente e verso: os ângulos `theta` e `theta + pi` representam o mesmo eixo. Interpolar ângulos comuns provoca inversões artificiais de 180 graus. Por isso, cada preferência local é representada pelo ângulo duplo:

```text
q(theta) = (cos(2 theta), sin(2 theta))
```

O motor combina preferências locais, com pesos próprios de cada candidato:

```text
q* = w_c q_contorno + w_l q_eixo_longo + w_b q_borda
q  = q* / ||q*||
coerencia = ||q*|| / soma_dos_pesos
```

`q_contorno` deriva da tangente perpendicular ao gradiente do MDT; `q_eixo_longo` deriva do eixo principal do bloco; `q_borda` regulariza a aproximação às bordas físicas. A coerência varia entre 0 e 1. Valor baixo significa que as preferências se anulam ou que não há direção axial local confiável.

As zonas topográficas, quando introduzidas futuramente, poderão modificar pesos e custos. Elas não recortarão o domínio e não gerarão linhas independentemente.

## 4. Levantamento de sinal e integrabilidade

O ângulo duplo precisa ser levantado para um campo vetorial local `n=(nx, ny)`. O sinal é propagado em uma vizinhança raster escolhendo, em cada ligação, a alternativa que mantém maior produto escalar com o vizinho. Ciclos incompatíveis revelam frustração: um campo axial pode parecer suave localmente e ainda assim não admitir uma fase global contínua.

O seed de fase é obtido por projeção de Poisson:

```text
phi_0 = arg min_phi integral_D ||grad(phi) - n||^2 dA
```

Na forma de Euler-Lagrange, com condições de contorno e uma fixação de gauge explícitas:

```text
Delta(phi_0) = div(n)
```

O resíduo de integrabilidade mede a parte do campo orientado que não pode ser representada pelo gradiente de uma única função escalar. Frustração de ciclos, resíduo angular e resíduo de integrabilidade são gates, não apenas pontuação.

O gauge é explícito e reprodutível:

```text
gauge_method = ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT
gauge_anchor_value_m = 0
```

Como a máscara válida deve ter uma única componente, o registro do candidato publica a âncora efetivamente usada em `phase_offset_selection.phase_gauge`. A fixação remove a constante arbitrária do sistema linear; ela não seleciona a melhor família de isolinhas e não deve ser confundida com a busca de offset descrita adiante.

## 5. Projeção alternada LSQR e resíduos, não solução eikonal

As isolinhas de uma fase arbitrária não mantêm espaçamento constante. Localmente, a distância normal entre níveis separados por `Delta phi` é aproximadamente:

```text
distancia_normal ~= Delta phi / ||grad(phi)||
```

Para usar níveis `phi = k s + o`, com espaçamento-alvo `s` e offset discreto `o`, o campo deve satisfazer aproximadamente a equação eikonal:

```text
||grad(phi)|| = 1
```

O protótipo CF0 atual não resolve um campo distância por fast marching, fast sweeping ou reinitialização eikonal não linear. Também não minimiza explicitamente uma energia com Hessiana. Ele usa uma aproximação alternada e auditável:

```text
method = ALTERNATING_POSITIVE_SCALE_PROJECTION
eikonal_role = RESIDUAL_GATE_ONLY
```

```text
1. resolver por LSQR: grad(phi) ~= lambda n
2. atualizar lambda a partir de grad(phi) . n
3. limitar lambda a um intervalo positivo
4. regularizar lambda em direção a 1, normalizar sua mediana e relaxar a atualização
5. repetir até a tolerância ou o número máximo de iterações
```

Essa escala positiva `lambda` permite acomodar parte da incompatibilidade sem inverter a direção levantada. A regularização em torno de 1 favorece, mas não garante exatamente, `||grad(phi)||=1`. Por isso, o motor mede `| ||grad(phi)|| - 1 |` e bloqueia pelo resíduo eikonal P95. `eikonal_role=RESIDUAL_GATE_ONLY` descreve esse uso limitado: o raster `phase` não deve ser chamado de solução eikonal nem de campo distância. A aproximação só passa quando todos os resíduos declarados atendem aos gates.

Uma simples multiplicação global de `phi` ou uma reescala dos valores de nível não corrigiria a variação espacial de `||grad(phi)||`, singularidades nem incompatibilidade do campo axial. Métodos eikonais mais completos permanecem uma evolução possível e deverão entrar com benchmark separado, sem rebatizar o solver LSQR atual.

### Níveis de fase e busca discreta de offset

Depois de fixado o gauge, a extração avalia exatamente quatro deslocamentos dentro de um espaçamento:

```text
phase_offset_fractions = [0, 0.25, 0.50, 0.75]
phase_offset_m = phase_offset_fraction * row_spacing_m
phase_level_m = phase_level_index * row_spacing_m + phase_offset_m
```

`phase_level_index` é um inteiro assinado no escopo `CANDIDATE_WORK_BLOCK_PHASE_LEVEL`. O mesmo nível pode produzir mais de um segmento desconectado, portanto esse índice pode se repetir. Já `row_index` é uma sequência inteira, única e não negativa dentro do candidato/bloco, conforme `UNIQUE_NONNEGATIVE_OPERATIONAL_SEQUENCE_PER_CANDIDATE_WORK_BLOCK`.

`row_index` **não** representa rota, ordem de trabalho, sequência do controlador, continuidade entre talhões, tiro, alcance hidráulico ou ordem de colheita. Qualquer um desses produtos exigirá outro grafo operacional e outro contrato.

Cada um dos quatro offsets produz um trial auditável com status, blockers, contagem e comprimento extraídos, raio, spacing, cobertura proxy, endpoints e topologia. Quando o offset zero comprova um gate invariável ao deslocamento da fase, os outros três trials permanecem no contrato, mas são encerrados sem materializar linhas, com `PHASE_OFFSET_PRUNED_BY_INVARIANT_GATE` e métricas nulas/zero fail-closed. Isso não se aplica a falhas que podem mudar com o offset, como raio, endpoints, cobertura ou topologia. A seleção considera somente trials `GEOMETRIC_PASS`, na ordem de chaves declarada: maior raio mínimo; menor fração de spacing fora da tolerância; menor erro P95; menor erro absoluto de cobertura proxy em relação a 1; e menor fração de offset. Se nenhum trial passar, o candidato fica `NO_FEASIBLE_FAMILY` e o offset diagnóstico é o menor configurado, apenas para explicar a falha.

Essa busca é `DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE`. Ela não amostra todos os deslocamentos possíveis e não prova invariância contínua ao gauge. O relatório deve mostrar o offset selecionado ou diagnóstico, os quatro trials e o estado da seleção sem chamá-los de ótimo contínuo.

O solver registra, entre outros diagnósticos:

- resíduo eikonal P95;
- resíduo de integrabilidade RMS/P95;
- erro angular P95;
- coerência mínima e fração de baixa coerência;
- conflitos do levantamento de sinal e frustração de ciclos;
- fração de gradiente crítico, singularidades e cut locus;
- convergência e número de iterações.

Falhar em um gate produz `NO_FEASIBLE_FAMILY`; o motor não força linhas para preencher o mapa.

A extrapolação `FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED` atua depois desse cálculo apenas para dar suporte à passagem do extrator de isolinhas pela vizinhança imediata do contorno. Valores no halo não integram as estatísticas ou o domínio da fase e não podem ser interpretados como solução fora do supercover. O QA por candidato registra quantas células de halo tiveram suporte direto ou LSQ, quantas ficaram sem suporte, o maior raio usado, resíduos e condicionamento observados.

## 6. Extração, spline C2 e raio

As linhas brutas são isolinhas:

```text
L_k = {(x,y) em D : phi(x,y) = k s + o}

k = phase_level_index; s = row_spacing_m; o = phase_offset_m
```

O esquema de marching triangles é apenas o extrator inicial. Seus vértices dependem da grade e não constituem uma trajetória dirigível. Cada linha passa por uma spline B paramétrica `C2`, com desvio máximo em relação à isolinha e permanência dentro do bloco físico.

Antes da spline, a isolinha auxiliada pelo halo é recortada contra a geometria física. Na etapa de término, `TANGENT_ONLY_FAIL_CLOSED` pode prolongar a tangente até essa borda; uma ligação lateral ao ponto mais próximo é proibida. Portanto, halo, recorte e extensão têm papéis diferentes e auditáveis.

A spline é ajustada com todos os vértices originais da linha de entrada. A representação usada para métricas e publicação segue `spline_representation_method=ADAPTIVE_CHORD_ERROR_PRESERVE_VERTICES`: nenhum vértice original é descartado, e novos pontos são inseridos adaptativamente até que o desvio entre cada arco representado e sua corda respeite:

```text
erro_de_corda <= spline_max_deviation_m * spline_representation_tolerance_fraction
erro_de_corda <= 0,12 m * 0,10 = 0,012 m
```

`spline_representation_tolerance_fraction=0.10` controla a fidelidade da discretização da spline, não o ajuste da spline ao traçado original. O limite geométrico `spline_max_deviation_m=0.12` continua valendo integralmente e não é ampliado para acomodar a representação. O refinamento também respeita o comprimento máximo de segmento declarado por `curvature_sample_step_m`.

Se a spline não puder ser ajustada, permanecer no domínio ou respeitar os limites de desvio e representação, o blocker único desse contrato é `ROW_SPLINE_FIT_FAILED`. O nome aposentado `C2_SPLINE_FAILED` é inválido e também confundiria continuidade matemática `C2` com o produto agronômico C2. Uma falha não autoriza publicar a isolinha bruta ou uma spline relaxada como substituta.

Para uma curva parametrizada `r(t)=(x(t),y(t))`, o estimador analítico de curvatura e raio é:

```text
kappa(t) = |x'(t)y''(t) - y'(t)x''(t)| / (x'(t)^2 + y'(t)^2)^(3/2)
R(t) = 1 / |kappa(t)|
```

Esse estimador não é usado sozinho. A `LineString` que representa a spline também é reamostrada em `curvature_sample_step_m` e recebe um estimador discreto de raio. O valor publicado em `min_radius_m` é conservador:

```text
min_radius_m = min(raio_analitico_da_spline, raio_reamostrado_da_LineString)
```

Assim, uma curvatura evidenciada pela geometria persistida não pode ser ocultada por uma estimativa analítica mais favorável, nem o inverso. O passo de reamostragem é parte versionada do contrato.

O contrato registra:

- método `PARAMETRIC_BSPLINE_C2`;
- tolerância de desvio da spline `spline_max_deviation_m=0.12`;
- representação `ADAPTIVE_CHORD_ERROR_PRESERVE_VERTICES`;
- fração de erro de corda `spline_representation_tolerance_fraction=0.10`;
- passo de amostragem da curvatura;
- tolerância de snap da extremidade;
- extensão máxima até a borda física;
- menor raio observado por linha e por família.

`fleet.minimum_work_path_radius_m` não possui default universal. Quando não informado, o motor pode medir o raio observado, mas publica `radius_status=NOT_EVALUATED` e a limitação `RADIUS_REQUIREMENT_NOT_PROVIDED`. Um valor infinito para reta é serializado como `null`; JSON nunca recebe `Infinity`.

O raio da linha trabalhando é diferente de `fleet.minimum_turn_radius_m`, utilizado para manobra em cabeceira.

O diagnóstico `coverage_proxy_ratio = comprimento_planimétrico_total * espaçamento / área_útil` usa os limites versionados `extraction.coverage_proxy_minimum` e `extraction.coverage_proxy_maximum`. Ele pode exceder 1 por efeitos de borda, fragmentação e aproximação geométrica; não é fração exata de área coberta e não deve ser apresentado como percentual de plantio. O mesmo princípio vale para tolerância de espaçamento, fração de área fora da tolerância e todos os demais gates: relatório e verificador leem o manifesto, sem repetir números fixos em código ou texto.

## 7. Cota, greide e continuidade

Somente depois de aprovada em 2D a linha é drapeada no MDT. A camada `continuous_rows` é obrigatoriamente `LineStringZ`, com todo XYZ finito e no CRS projetado em metros declarado no manifesto.

Para cada linha são calculados comprimento planimétrico XY, perfil e cotas Z, greide longitudinal absoluto máximo/P95, extensão em greide adverso e reversões. Esses valores ajudam a comparar candidatos e localizar risco de concentração. Eles não são prova de capacidade hidráulica.

Os gates topológicos impedem:

- auto-interseção;
- loop fechado;
- cruzamento, toque ou sobreposição entre linhas da mesma família;
- geometria fora do bloco;
- endpoint interno sem suporte físico;
- linha 2D, vazia, inválida ou com coordenada não finita;
- duplicidade de `row_id`, `family_id` ou candidato por bloco.

## 8. Estados contratuais

| Campo | Estado | Significado |
|---|---|---|
| `geometric_status` | `GEOMETRIC_PASS` | fase, offset selecionado, extração, espaçamento e topologia CF0 passaram; somente suas linhas entram em `continuous_rows`, sem autorização operacional |
| `geometric_status` | `NO_FEASIBLE_FAMILY` | ao menos um gate geométrico falhou; nenhuma linha desse candidato entra em `continuous_rows` |
| `hydraulic_status` | `HYDRAULIC_UNCONFIRMED` | obrigatório em todo CF0, inclusive quando os insumos hidráulicos estão completos |
| `radius_status` | `PASS` | requisito estático da frota foi declarado e atendido |
| `radius_status` | `FAIL` | requisito declarado não foi atendido; a família não pode passar |
| `radius_status` | `NOT_EVALUATED` | requisito da frota não foi fornecido |
| `diagnostic_status` | `NOT_APPROVED` | obrigatório em toda feição de `diagnostic_rows`; geometria preservada apenas para explicar uma rejeição |
| `guidance_status` | `NOT_AUTHORIZED` | obrigatório em toda feição de `diagnostic_rows`; proíbe exportação como guiamento |

No nível do estágio:

- se ao menos um candidato tiver `GEOMETRIC_PASS`, `stage_status=HYDRAULIC_UNCONFIRMED`;
- se nenhum candidato for geometricamente viável, `stage_status=NO_FEASIBLE_FAMILY`;
- não existe `HYDRAULIC_PASS` no vocabulário CF0.

## 9. Pré-checagem hidráulica

`hydraulic_precheck` inventaria presença, ausência ou falta de revisão para:

1. chuva de projeto/IDF;
2. infiltração e parâmetros do solo;
3. bacia contribuinte completa, inclusive contribuição externa;
4. outlets e rede de drenagem;
5. receptor estável e condição de jusante;
6. seção de sulco, terraço, CEV ou estrutura;
7. rugosidade;
8. travessias, carreadores, bueiros e demais controles.

Mesmo que os oito itens estejam `PROVIDED`, o registro mantém `CF0_NO_HYDRAULIC_SOLVER`. O CF0 não calcula chuva excedente, vazão, profundidade, velocidade, tensão de cisalhamento, extravasamento, deposição ou caminho de falha.

Não é permitido converter greide baixo, ausência de reversões ou boa orientação ao contorno em “hidráulica aprovada”.

## 10. Artefatos e contrato

O estágio publica:

```text
dataset/derived/continuous_family_manifest.json
dataset/derived/continuous_family_candidates.gpkg
dataset/derived/continuous_family_map.png
dataset/derived/continuous_family_rasters/
  {candidate}__{field}__{work_block}__orientation_coherence.tif
  {candidate}__{field}__{work_block}__phase.tif
```

O raster `orientation_coherence` possui duas bandas:

1. orientação axial da linha em graus, no intervalo `[0,180)`;
2. coerência, no intervalo `[0,1]`.

O raster `phase` possui uma banda em metros. Os dois rasters de um candidato/bloco de trabalho compartilham dimensão, geotransform, NoData e CRS.

### Camadas GeoPackage

O GeoPackage CF0 1.2 contém exatamente quatro camadas contratuais. `diagnostic_rows` é obrigatória mesmo quando vazia; ausência da camada invalida o pacote. No mapa e no relatório, `continuous_rows` deve aparecer como **linhas aprovadas no gate geométrico CF0**, enquanto `diagnostic_rows` deve usar simbologia distinta e o selo **linhas diagnósticas, não aprovadas**. Misturar as duas classes ou usar a mesma legenda invalida a leitura do produto.

`continuous_rows` (`LineStringZ`):

```text
row_id, family_id, candidate_id, field_id, work_block_id,
row_index, phase_level_index, phase_level_m, phase_offset_m,
phase_offset_fraction, length_m, min_radius_m,
max_abs_grade_pct, grade_p95_pct, reversal_count, start_surface, end_surface,
geometry_status, hydraulic_status, topology_status, blocker_codes
```

`min_radius_m` é o menor valor conservador entre o raio analítico da spline C2
e o raio obtido da `LineStringZ` reamostrada em `curvature_sample_step_m`. O
verificador rejeita valor declarado maior que o sustentado pela geometria
persistida além da tolerância de discretização. Em uma família
`GEOMETRIC_PASS`, todo raio finito declarado também deve atender ao requisito
da frota. Segmentos sem curvatura finita permanecem `null`.

`diagnostic_rows` (`LineStringZ`, nunca aprovada):

```text
mesmos campos de índice, fase, métricas e geometria de continuous_rows,
diagnostic_status=NOT_APPROVED, guidance_status=NOT_AUTHORIZED
```

Ela só pode conter geometria de candidatos `NO_FEASIBLE_FAMILY`. Essas feições explicam blockers e métricas; não são linhas executivas, não entram na contagem publicada e nunca preenchem visualmente um cenário ausente.

`family_summary` (sem geometria):

```text
candidate_id, field_id, work_block_id, family_id,
phase_offset_m, phase_offset_fraction, phase_offset_role,
phase_offset_selection_status,
geometry_status, hydraulic_status, row_count, total_length_m,
diagnostic_row_count, diagnostic_total_length_m,
spacing_p95_m, eikonal_p95, integrability_p95,
orientation_p95_deg, min_radius_m, radius_status, max_grade_pct,
internal_endpoints, intersections, self_intersections, loops, blocker_codes
```

O `family_summary` oferece apenas a projeção tabular dos campos principais. A evidência completa permanece em cada item de `candidates`: `phase_offset_selection` contém gauge, quatro trials, regra e estado de seleção; `contour_extrapolation_qa` contém o contrato e as medições do halo; `metrics.solver_gate_metrics.solve_mask_component_count` contém a contagem observada da máscara.

`hydraulic_precheck` (sem geometria):

```text
candidate_id, field_id, work_block_id, family_id, hydraulic_status,
idf_status, soil_status, contributing_area_status, outlet_status,
receiver_status, section_status, roughness_status, downstream_status,
grade_p95_pct, grade_max_pct, reversal_count, adverse_length_pct,
missing_inputs, blocker_codes
```

O manifesto registra parâmetros, candidatos viáveis e inviáveis, QA, contagens, limitações, montagem dos blocos e integridade. Arquivos simples usam SHA-256 e tamanho. Um SHP usa `source_bundle_sha256` derivado de todos os sidecars declarados; o verificador nunca compara o hash do bundle apenas com o `.shp`.

O vínculo não termina no texto do manifesto. O verificador relê o pedido resolvido, recompõe seu SHA-256 e confere `request_id`; recompõe `crs.wkt_sha256` a partir do WKT exportado pela fonte de área de trabalho; confere semanticamente o CRS do MDT, GeoPackage e rasters; e exige que o tamanho, orientação e alinhamento do pixel de cada raster correspondam a `solver_parameters.grid_resolution_m`. `absolute_barrier_count` é a quantidade de datasets em `inputs.absolute_barriers`, não a quantidade de feições dentro de cada arquivo.

## 11. Gates de publicação

| Gate | Condição de passagem |
|---|---|
| integridade | hashes, tamanhos e paths conferem; o hash do pedido e o hash WKT são recompostos das fontes resolvidas |
| CRS | CRS projetado em metros; GeoPackage, MDT, área e rasters coincidem |
| cobertura MDT | nenhuma célula útil fora do MDT ou em NoData |
| domínio | `work_block_id` conhecido e geometria contida nele |
| máscara de solução | exatamente uma componente 4-conexa; divergência produz `WORK_BLOCK_SOLVE_MASK_DISCONNECTED` |
| orientação axial | coerência e frustração dentro das tolerâncias |
| integrabilidade | resíduo dentro do limite e sem conflito crítico |
| eikonal | `||grad(phi)||` próximo de 1 dentro do limite P95 |
| offset de fase | quatro trials discretos preservados; somente PASS pode ser selecionado pela ordem declarada |
| spacing | erro normal P95 menor ou igual à tolerância declarada |
| cobertura proxy | `coverage_proxy_ratio` entre os limites declarados no manifesto; não é fração areal |
| spline | curva C2 dentro do domínio e do desvio máximo; falha produz `ROW_SPLINE_FIT_FAILED` sem fallback geométrico |
| representação da spline | todos os vértices originais preservados e erro de corda menor ou igual a 10% do desvio máximo de 0,12 m |
| halo de contorno | suporte LSQ local dentro dos limites de raio, vizinhança, rank, resíduo e condição; falha produz `PHASE_HALO_GRADIENT_UNESTIMABLE` |
| raio | `min_radius_m` é o mínimo conservador entre estimativa analítica e LineString reamostrada; `PASS` se o requisito existe, `NOT_EVALUATED` e limitação se não existe |
| topologia | zero loops, auto-interseções e interseções entre linhas |
| endpoints | zero extremidades internas sem superfície física |
| 3D | `LineStringZ`, XYZ finito e comprimento planimétrico coerente |
| hidráulica | sempre `HYDRAULIC_UNCONFIRMED` no CF0 |

O verificador independente está em `scripts/verify_continuous_family.py`. Ele exige as quatro camadas, cruza manifesto, hashes, bundle SHP, rasters, CRS, atributos, contagens aprovadas e diagnósticas, Z, comprimentos, raios amostrados, limites dos blocos, endpoints e topologia. Ele também reconstrói a partição de componentes incluídos/excluídos, recompõe `qa.gate_failures` como a união ordenada dos `blocker_codes` dos candidatos e deriva blockers diretamente das métricas quando o gate foi efetivamente avaliado. Qualquer divergência encerra com código diferente de zero.

O relatório técnico CF0 1.2 copia para seu próprio manifesto os contratos de gauge/máscara, níveis/offsets, borda LSQ, blockers e representação da spline. Também copia, candidato a candidato, o offset selecionado ou diagnóstico, todos os trials, o QA do halo e a contagem de componentes da máscara. Páginas específicas mostram esses valores, distinguem linhas aprovadas de diagnósticas e os exigem como texto pesquisável no preflight do PDF.

O verificador do relatório compara cada cópia com o manifesto CF0 fonte, confirma que `spline_max_deviation_m` continua em `0.12`, exige os limites LSQ `0.30` e `100.0`, a máscara 4-conexa unitária, o blocker `ROW_SPLINE_FIT_FAILED` e a semântica não operacional de `row_index`. A ausência ou troca de qualquer valor, um modo de conexão lateral, a omissão de um trial, uma falsa alegação de invariância contínua ou uma fração de representação maior invalida o relatório.

No ambiente QGIS 3.32 usado no projeto, o pacote Python `jsonschema` não está disponível. Nesse caso, o verificador reporta `IMPERATIVE_CONTRACT_CHECKS_ONLY_JSONSCHEMA_UNAVAILABLE` e executa suas checagens imperativas explícitas; isso não é apresentado como validação genérica equivalente de todo o Draft 2020-12. Em um ambiente com `jsonschema`, a instância passa primeiro pelo schema Draft 2020-12 e depois pelos mesmos vínculos semânticos e espaciais, que JSON Schema isoladamente não expressa.

## 12. Evolução para os produtos reais

No contrato e no relatório CF0 1.2, os três produtos permanecem explicitamente `NOT_GENERATED`: nenhuma geometria C1, C2 ou C3 é inferida, desenhada ou aprovada a partir das linhas CF0.

### C1 - Curva embutida

Usará uma família CF0 como alinhamento candidato, mas precisará definir espaçamento vertical/horizontal, seção, capacidade, destino do excedente, interferência com sulcação e forma de implantação. O dimensionamento dependerá de chuva, solo, infiltração, comprimento de rampa, área contribuinte e receptor. O resultado será estrutura conservacionista calculada, não somente uma isolinha.

### C2 - Base larga/passante

Acrescentará seção larga e rasa, volume de armazenamento/condução, estabilidade, taludes e verificação de trânsito de máquinas e implementos sobre o camalhão. “Passante” descreve função operacional de uma seção dimensionada; não significa liberar cruzamento sobre qualquer linha desenhada.

### C3 - ESD

Será um sistema PCE/PCX: greide controlado distribui o escoamento entre muitos sulcos, que se conectam a outlets e receptores seguros. O cálculo deverá representar contribuições externas, convergência, transferência sulco-receptor, CEV quando necessário, excedência e caminho de falha. ESD não é uma linha amarela, uma curva de nível nem ausência de terraços.

### Ordem de desenvolvimento

1. homologar o CF0 em áreas sintéticas e em conjuntos locais controlados;
2. obter raio estático da plantadora e superfícies operacionais;
3. calibrar a comparação CF0A-C com agrônomo e operação;
4. implementar hidrologia de evento e rede de receptores;
5. dimensionar C1, C2 e C3 como produtos separados;
6. validar em campo, registrar revisão profissional e somente então exportar guiamento.

## 13. Insumos necessários para promover um projeto

- `fleet.minimum_work_path_radius_m` por operação/equipamento;
- inventário ou declaração formal de ausência de rede elétrica;
- carreadores, cabeceiras, portais, cercas, valas e obstáculos revisados;
- chuva de projeto/IDF e duração/recorrência;
- infiltração, textura, erodibilidade, umidade e manejo do solo;
- bacia contribuinte além do recorte do talhão;
- outlets, receptores, talvegues, APPs, drenagens e condição de jusante;
- seções e rugosidades de sulcos, terraços, CEV, vias e bueiros;
- controle altimétrico e validação do MDT;
- validação de campo e responsabilidade técnica.

Enquanto esses itens faltarem, linhas em `continuous_rows` são apenas candidatos geométricos publicados para análise comparativa. Linhas em `diagnostic_rows` permanecem rejeitadas, ainda que sejam úteis para localizar e explicar os gates que falharam.

## 14. Base técnica consultada

As referências sustentam princípios e métodos; nenhum valor observado em estudo de caso é convertido em promessa ou default universal.

- Spekken, De Bruin, Molin e Sparovek. [Planning machine paths and row crop patterns on steep surfaces to minimize soil erosion](https://research.wur.nl/en/publications/planning-machine-paths-and-row-crop-patterns-on-steep-surfaces-to/). Estudo primário aplicado a cana em São Paulo: referências híbridas, linhas paralelas dirigíveis e avaliação de escoamento/perda de solo.
- Song et al. [Continuous curvature wayline generation using quadratic optimisation for agricultural machine guidance](https://www.sciencedirect.com/science/article/pii/S1537511023002623). Pesquisa primária para restrições simultâneas de curvatura, skips e overlaps; fundamenta otimização contínua em vez de offsets geométricos cegos.
- Instituto Agronômico. [Boletim Técnico IAC 216 - Recomendações gerais para a conservação do solo na cultura da cana-de-açúcar](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf). Fonte oficial para PCE/PCX, sulcação, tipos de terraço, capacidade, drenagem e canais escoadouros.
- Agência Nacional de Águas e Saneamento Básico. [Manual do Programa Produtor de Água, volume 5](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5). Fonte oficial para planejamento integrado e práticas mecânicas, inclusive ESD.
- Franco, ESALQ/USP. [Funcionamento hidráulico do terraceamento e Escoamento Superficial Difuso](https://www.teses.usp.br/teses/disponiveis/11/11140/tde-21012019-150102/publico/Alexandre_Puglisi_Barbosa_Franco_versao_revisada.pdf). Pesquisa primária sobre infiltração, falhas, sulcos como condutos e integração PCE/PCX; também documenta a incerteza experimental das receitas gerais.

Essas fontes reforçam a decisão central do CF0: geometria de cobertura, dirigibilidade e conservação precisam ser avaliadas em conjunto, mas aprovação hidráulica exige outro estágio, outros insumos e outro contrato.
