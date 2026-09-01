# Motor de Cenários, Terraços e Sulcação

## 1. Objetivo e posição no produto

Este documento especifica um motor computacional para propor, simular, comparar e revisar arranjos de conservação do solo e mecanização em cana-de-açúcar. O escopo inclui terraços embutidos, terraços de base larga e passantes, terraços de drenagem, Canal Escoadouro Vegetado (CEV), Escoamento Superficial Difuso (ESD), sulcação em contorno ou com declividade controlada e tiros operacionais atravessando mais de um talhão.

O motor não deve ser apresentado como substituto autônomo do projeto agronômico e hidráulico. Ele é um sistema de apoio ao projeto, com regras versionadas, memória de cálculo, simulação e revisão profissional.

A definição dos componentes, a cadeia física da água, os gates e a interpretação das linhas de CAD estão consolidados em [Lógica do ESD e do Canal Escoadouro](LOGICA_ESD_E_CANAL_ESCOADOURO.md).

Para distinguir evidência de decisão de produto, o texto usa quatro marcadores:

- **[BASE TÉCNICA]**: orientação ou método encontrado em fonte técnica oficial ou literatura científica citada.
- **[PROPOSTA]**: decisão recomendada para a arquitetura do produto; precisa ser validada durante o desenvolvimento.
- **[INFERÊNCIA]**: conclusão derivada das fontes, dos dados ou de princípios de engenharia; não é uma prescrição literal da fonte.
- **[BLOQUEIO]**: condição que impede dimensionamento ou liberação para execução.

**[BASE TÉCNICA]** O IAC separa o Projeto de Controle da Erosão (PCE) do Projeto de Controle da Enxurrada (PCX). O primeiro orienta práticas e espaçamento; o segundo verifica volume, vazão, velocidade, armazenamento e capacidade das estruturas. O sistema deve preservar essa separação, embora os dois cálculos precisem ser iterados. [IAC, Boletim Técnico 216, especialmente pp. 74-100](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf)

**[PROPOSTA]** A unidade de decisão será um `scenario`, contendo superfície, rede hidráulica, estruturas, sulcos, operação de máquinas, parâmetros, resultados, violações, incertezas e aprovações em uma versão imutável. Um cenário nunca será apenas um conjunto de linhas vetoriais.

## 2. Princípio central: continuidade operacional não é continuidade hidráulica

Um tiro longo pode ser vantajoso para mecanização e, ao mesmo tempo, perigoso para conservação. A plataforma deve representar separadamente três entidades:

### 2.1 Linha guia

`guidance_line` é a trajetória navegável pelo veículo ou piloto automático. Pode atravessar cabeceiras, carreadores e mais de um talhão. Ela descreve direção, curvatura e navegação, não afirma que o implemento permanecerá no solo em todo o percurso.

Campos mínimos:

```text
guidance_line
  id, scenario_id, guidance_group_id
  geometry: LineStringZ
  direction, nominal_spacing
  curvature_stats, grade_stats
  machine_profile_id
  operational_run_id
  qa_status, source, version
```

### 2.2 Segmento trabalhado

`worked_segment` é exclusivamente o trecho em que o implemento está engatado e executa sulcação, plantio ou colheita. Uma linha guia pode conter vários segmentos trabalhados, separados por terraços, estradas, CEV, obstáculos ou manobras fisicamente classificados. Fronteiras de zonas analíticas ou simples recortes de talhões não são superfícies de parada.

```text
worked_segment
  id, guidance_line_id
  farm_id, legal_land_unit_id, field_id, zone_id
  operation: FURROW | PLANT | HARVEST
  geometry: LineStringZ
  implement_state: ENGAGED
  work_depth, permission_refs
  start_reason, end_reason
```

### 2.3 Segmento de movimento

`movement_segment` registra o deslocamento sem trabalho. Ele permite construir uma guia operacional contínua sem fingir que existe cana ou sulco físico no carreador, na cabeceira ou no intervalo entre propriedades.

```text
movement_segment
  id, guidance_line_id, sequence
  operation: LIFT | CROSS | TURN | TRANSIT
  crossing_type: CARRIER | TERRACE_PASS | INTER_FIELD | INTER_FARM | NONE
  portal_id, geometry: LineStringZ
  implement_state: RAISED | DISENGAGED
  speed_limit, permission_refs
```

### 2.4 Alcance hidráulico

`hydraulic_reach` é o trecho contínuo pelo qual a água pode escoar sem interceptação, dissipação ou descarga controlada. Seus limites podem não coincidir com os talhões nem com os segmentos trabalhados.

```text
hydraulic_reach
  id, scenario_id, carrier_type
  carrier_id, geometry: LineStringZ
  upstream_node_id, downstream_node_id
  contributing_area, hydraulic_length
  q_peak, volume, depth, velocity, shear
  capacity, safety_margin, failure_destination
```

**[INFERÊNCIA]** Um tiro operacional de 900 m pode ser aceitável se o implemento levantar em travessias e a rede dividir o escoamento em alcances hidráulicos seguros. Em sentido inverso, vários tiros curtos podem formar um caminho hidráulico longo se descarregarem sucessivamente no mesmo sulco, trilha ou carreador.

**[PROPOSTA]** A interface sempre exibirá `comprimento_operacional` e `maior_alcance_hidráulico` como métricas diferentes. Nenhuma aprovação será baseada somente no comprimento geométrico da linha.

Invariante do modelo:

```text
linha guia contínua != trabalho contínuo != alcance hidráulico contínuo
fronteira analítica != superfície operacional de parada ou manobra
```

**[GATE DURO]** Uma linha trabalhada não pode terminar ou reiniciar no interior cultivável sem uma superfície operacional que suporte esse estado. Cada endpoint deve tocar a borda interna de uma cabeceira ou uma barreira classificada que exija interrupção. Endpoint interno sem suporte, cruzamento, sobreposição ou loop reprova a família; passar nesse gate geométrico não autoriza a manobra, que depende de frota, envelope e superfície aprovados.

## 3. Modelo de domínio integrado

### 3.1 Objetos principais

```text
scenario
  terrain_revision
  rule_pack_revision
  hydrology_run
  erosion_run
  terrace_network
    terrace_alignment
    terrace_section
    crossing_node
  drainage_network
    cev_alignment
    natural_receiver
    outlet
    grade_control
    road_drainage
  road_network
    carrier_surface
    platform_edge
    ditch
    culvert
    road_outlet
  esd_system
    esd_zone
    esd_control_line
    furrow_family
    furrow_reach
    transfer_node
  row_plan
    guidance_line
    worked_segment
    movement_segment
    hydraulic_reach
    headland
    turn
  metric
  violation
  review_decision
```

Cada estrutura proposta deve ter, no mínimo: tipo; `LineStringZ`; estaqueamento; declividade prevista e medida; seção transversal; largura, profundidade e taludes; bordo livre; rugosidade; área contribuinte; nó de saída; volume e vazão de projeto; capacidade; fator de segurança; sequência construtiva; fonte dos parâmetros; incerteza; estado de revisão.

Uma estrutura de terraço não será persistida em um enum composto. Ela terá eixos independentes:

```text
terrace
  hydraulic_function: TI | TD
  cross_section: EMBUTIDA | BASE_LARGA | BASE_MEDIA | ESTREITA | INVERTIDA
  longitudinal_control: NIVEL_FECHADO | NIVEL_ABERTO | GREIDE_CONTROLADO
  construction_method: NICHOLS | MANGUM | NICHOLS_INVERTIDO | OUTRO
  operability: NAO_PASSANTE | PASSANTE_CONDICIONAL | TRAVESSIA_LOCAL
  state: EXISTENTE | PROPOSTO | AS_BUILT | DEGRADADO

CEV
NATURAL_RECEIVER
CANAL_REVESTIDO
ROAD_DRAINAGE
CONTROLE_DE_GREIDE
OUTLET
CROSSING_NODE
```

**[PROPOSTA]** `TI` e `TD` indicam comportamento hidráulico de infiltração e drenagem, não apenas a aparência da seção. O mesmo template geométrico não deve mudar silenciosamente de função conforme o cenário.

### 3.2 Superfícies e grafos

O motor deve manter simultaneamente:

- DTM raster para cálculo distribuído do escoamento.
- TIN para perfis, seções e volumes de corte/aterro.
- `G_hidraulico`, com células contribuintes, sulcos, transferências, terraços, CEV, estradas, bueiros, receptores, saídas e caminhos de falha.
- `G_operacional`, com linhas-guia, segmentos trabalhados, cabeceiras, carreadores, manobras e estados do implemento.
- superfície original imutável e uma superfície proposta por cenário.

**[INFERÊNCIA]** Não é suficiente desenhar linhas sobre o DTM original e calcular água apenas uma vez. Terraços, sulcos, trilhas e canais alteram o terreno e a conectividade. A superfície proposta deve ser reconstruída e o evento de chuva novamente simulado.

## 4. Insumos e contrato mínimo

### 4.1 Terreno e contexto espacial

- Polígonos dos talhões, limites operacionais e áreas excluídas classificados.
- Nuvem LAS/LAZ/COPC ou DTM, com CRS, unidades e datum vertical declarados.
- Pontos independentes de checagem vertical e relatório de acurácia.
- Área suficiente a montante e a jusante para fechar as microbacias relevantes.
- Breaklines, cursos d'água, áreas úmidas, APP, estradas, carreadores, valetas, bueiros, obras existentes e saídas.

### 4.2 Solo, chuva e manejo

- Unidades de solo, textura, profundidade efetiva, infiltração/permeabilidade, erodibilidade, compactação e hidromorfia.
- IDF ou hietograma oficial, duração, distribuição temporal e tempos de retorno aprovados.
- Cultura, cobertura, palhada, preparo, tráfego e condição entre safras.
- Parâmetros medidos, informados, inferidos e assumidos marcados separadamente.

### 4.3 Máquina e operação

- Largura e offset do implemento, espaçamento entre linhas e bitola.
- Raio mínimo, comprimento do conjunto, envelope de manobra e cabeceira.
- Limites de rampa, rolagem, quebra vertical e clearance.
- Regras de tráfego controlado, sentido preferencial, velocidade e sequência de trabalho.
- Capacidade real de construir e manter cada seção proposta.

**[BLOQUEIO]** Ortomosaico sem DTM não sustenta greides, perfis ou dimensionamento. DTM sem datum e checkpoints pode sustentar triagem, mas não locação executiva. Polígono do talhão sem a microbacia externa não fecha o balanço de água.

## 5. Pipeline computacional

### 5.1 Ingestão e controle de qualidade

1. Preservar originais com hash, metadados e controle de versão.
2. Validar formato, CRS horizontal, unidades, datum vertical, cobertura e consistência espacial.
3. Classificar solo nu, remover ruídos documentados e produzir COPC, COG e TIN.
4. Comparar o DTM com checkpoints independentes e produzir uma camada de incerteza espacial.
5. Classificar vazios internos, bordas e feições que não podem ser atravessadas.

**[PROPOSTA]** A tolerância topográfica será relativa à menor queda que se pretende medir ou impor. Se a incerteza vertical for comparável à queda de projeto, o estágio executivo será bloqueado. Uma rampa de 0,3% representa apenas 0,30 m de queda em 100 m.

### 5.2 Condicionamento hidrológico

1. Derivar gradiente, aspecto, curvaturas e rugosidade.
2. Incorporar breaklines, bueiros e valetas confirmados.
3. Identificar e preservar depressões reais; preencher ou romper apenas artefatos justificados.
4. Calcular direções MFD ou D∞, acumulação, talvegues, divisores, microbacias e pontos de entrada/saída.
5. Emitir bloqueio quando contribuição externa ou destino a jusante forem desconhecidos.

### 5.3 Chuva-vazão e erosão

1. Calcular chuva excedente com modelo configurável, como Green-Ampt, Horton ou CN calibrado.
2. Distribuir e propagar o escoamento pelo terreno e pela rede linear.
3. Avaliar volume, pico, lâmina, velocidade, tensão de cisalhamento, armazenamento, extravasamento e destino de falha.
4. Executar PCE com RUSLE ou método equivalente para comparação espacial de risco.
5. Executar PCX em cada estrutura e na rede completa.

**[PROPOSTA]** O método racional poderá produzir pico preliminar em pequenas contribuições, mas não substituirá o hidrograma de evento quando armazenamento, encadeamento ou duração crítica alterarem a resposta. O motor testará várias durações de chuva, não apenas um evento nominal.

### 5.4 Geração, seleção e realimentação

1. Gerar candidatos de terraços, CEV, linhas-mestras, cabeceiras e crossings.
2. Eliminar candidatos que violem restrições duras.
3. Selecionar a topologia da rede por otimização discreta.
4. Refinar a geometria por otimização contínua.
5. Aplicar as seções à superfície proposta e recalcular volumes.
6. Rodar novamente PCE, PCX e operação.
7. Repetir até convergência ou limite documentado.
8. Publicar alternativas não dominadas e suas violações remanescentes.

## 6. Geração e dimensionamento de terraços

### 6.1 Regras de engenharia

**[BASE TÉCNICA]** O IAC distingue terraços de infiltração, em nível e com extremidades fechadas, de terraços de drenagem, com declividade e descarga em saída estável. Para solos de menor permeabilidade, o manual orienta sistemas de drenagem e a implantação prévia do canal escoadouro. Estradas não devem ser tratadas indiscriminadamente como escoadouros. [IAC, pp. 75 e 89-99](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf)

**[BASE TÉCNICA]** O terraço embutido é mais estreito e profundo e interrompe a continuidade dos sulcos. O terraço de base larga utiliza uma seção larga e suave, com maior aproveitamento agrícola. Uma seção passante permite travessia por máquinas, mas continua sujeita à capacidade hidráulica, compactação e erosão em trilhas. [IAC, pp. 75-77](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf) e [Embrapa, manejo e conservação em cana-de-açúcar](https://www.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/cana-de-acucar/producao/correcao-e-adubacao/manejo-e-conservacao)

**[PROPOSTA]** Limites de declividade, velocidade, espaçamento e tempo de retorno serão entradas de um `rule_pack` regional, aprovadas pelo responsável técnico. Valores de manuais não serão constantes universais no código.

### 6.2 Geração de candidatos

1. Dividir a vertente em zonas homogêneas de solo, declividade, cobertura e contribuição.
2. Usar a regra PCE escolhida para estimar o primeiro espaçamento vertical.
3. Gerar sementes em bandas de cota, linhas existentes e pontos de conexão permitidos.
4. Para infiltração, buscar caminhos quase em nível e bacias de armazenamento fechadas.
5. Para drenagem, buscar caminhos com greide monotônico até um CEV verificado.
6. Ajustar a linha ao terreno, limites e obstáculos, mantendo continuidade e raio construtivo.
7. Recalcular a bacia realmente interceptada por cada candidato.
8. Dimensionar seção e rejeitar o candidato quando a máquina não puder construí-lo ou mantê-lo.

O espaçamento fornecido pelo PCE é uma semente. A aceitação depende do PCX. Caso a seção necessária ultrapasse a capacidade construtiva, o motor deve reduzir o espaçamento, redistribuir a contribuição ou trocar a solução.

### 6.3 Seleção de rede

**[PROPOSTA]** Usar um modelo híbrido, em vez de tentar resolver toda a geometria em uma única otimização:

- MILP ou CP-SAT para selecionar candidatos, atribuir áreas contribuintes e escolher outlets.
- SQP, spline cúbica ou clotoide para refinar alinhamento, greide e curvatura.
- simulação hidráulica externa como oráculo de viabilidade em ciclos de corte e reotimização.

Variáveis discretas incluem seleção de terraço, tipo de seção, conexão ao CEV e ativação de crossing. Restrições duras incluem:

- capacidade ou armazenamento maior que a demanda com margem definida;
- limite do alcance hidráulico e da área contribuinte;
- saída verificada e capaz de receber a descarga;
- ausência de reversões ou pontos baixos não planejados;
- compatibilidade de solo, rampa, seção, equipamento e manutenção;
- falha sem cascata para estrutura inferior desprotegida;
- proteção de APP, cursos d'água, infraestrutura e propriedade vizinha.

A função objetivo combina movimento de terra, comprimento de estruturas, área agrícola perdida, número de interrupções, manutenção, tempo operacional e risco residual. Os pesos mudam entre cenários; as restrições duras não.

### 6.4 Falha e robustez

Cada rede deve ser testada com:

- chuva nominal e eventos de sensibilidade;
- estrutura parcialmente assoreada;
- obstrução local;
- solo úmido ou armazenamento remanescente do evento anterior;
- vegetação ainda não estabelecida;
- tolerância de execução na cota e seção;
- entalhe por trilha de roda em travessia;
- falha de um elemento e propagação a jusante.

O resultado deve mostrar onde a água irá quando uma capacidade for excedida. Ocultar a direção da falha é uma violação de aprovação.

## 7. CEV e Escoamento Superficial Difuso

### 7.1 Canal Escoadouro Vegetado

O CEV deve ser modelado como estrutura receptora progressiva, não como uma linha cartográfica. Seu perfil acumula descargas de terraços, sulcos, carreadores, estradas e bueiros.

**[BASE TÉCNICA]** O IAC orienta localizar o canal em corredor adequado, dimensionar área contribuinte, vazão, declividade, vegetação, velocidade permissível e seção, com verificação por Manning e bordo livre. O canal deve ser construído e estabilizado antes das estruturas que descarregam nele. [IAC, pp. 95-99](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf)

**[PROPOSTA]** O gerador irá:

1. buscar corredores candidatos próximos a talvegues, excluindo conflitos ambientais e geotécnicos;
2. estabelecer estações de controle ao longo do perfil;
3. acumular hidrogramas de todas as entradas;
4. dimensionar seção parabólica, trapezoidal ou triangular por trecho;
5. verificar profundidade, bordo livre, velocidade e tensão admissíveis para solo e cobertura;
6. aumentar a seção a jusante ou inserir controle de greide e dissipação;
7. rejeitar o corredor quando não houver condição estável de descarga.

**[INFERÊNCIA]** O CEV não é obrigatório em todo cenário ESD. A restrição universal é que toda descarga termine em receptor estável e verificado. O PCX, a variante regional e as condições ambientais determinam se o receptor será CEV, drenagem natural protegida ou outra estrutura dimensionada.

### 7.2 ESD

**[BASE TÉCNICA]** O ESD é um sistema de Escoamento Superficial Difuso, com plantio de greide controlado e rugosidade distribuída. O projeto completo inclui hidrologia da bacia, verificação hidráulica, alinhamento das linhas, receptores e drenagem separada de estradas e carreadores. O manual relaciona CEV `se houver`, portanto sua necessidade deve ser demonstrada pelo projeto. A implantação deve ser coordenada para não expor toda a vertente simultaneamente. [ANA, Manual do Programa Produtor de Água, volume 5](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5)

**[BASE TÉCNICA]** A literatura primária da ESALQ/USP descreve as linhas de plantio como canais rasos que conduzem enxurrada pela rugosidade do sulco. Também alerta que cópias sucessivas de uma linha básica podem adquirir greides altos ou convergir, e enquadra o ESD em PCE/PCX completos, com destino em canais escoadouros ou redes naturais de drenagem. [Franco, 2018, pp. 19 e 45-49](https://www.teses.usp.br/teses/disponiveis/11/11140/tde-21012019-150102/publico/Alexandre_Puglisi_Barbosa_Franco_versao_revisada.pdf)

**[INFERÊNCIA]** ESD não significa simplesmente remover terraços para obter tiros longos. É uma rede PCE/PCX na qual muitos sulcos conduzem pequenas parcelas de água até receptores verificados, evitando concentração não dimensionada.

**[PROPOSTA]** Linha de controle, sulco, carreador, CEV e linha-guia serão objetos distintos. Linhas importadas terão `control_role` e `hydraulic_role`; cor de CAD será apenas estilo. Uma geometria `UNCONFIRMED` não poderá transportar vazão no modelo nem autorizar implantação.

**[PROPOSTA]** Cada sulco será uma aresta hidráulica acoplada às células laterais do raster. O solver deverá calcular contribuição lateral, vazão acumulada, lâmina, velocidade, tensão, transbordamento entre linhas e carga simultânea no CEV. Restrições obrigatórias:

- greide longitudinal dentro do intervalo aprovado;
- ausência de depressões e reversões imprevistas;
- capacidade por sulco e por outlet;
- limite de alcance e largura contribuinte por sulco;
- distribuição sem concentração excessiva em poucas linhas;
- drenagem compatível de estradas, cabeceiras e crossings;
- saída estável para todos os caminhos de excesso.

Indicadores de difusão incluem coeficiente de variação e Gini das vazões, participação dos 5% de sulcos mais carregados, maior área contribuinte, maior descarga, maior alcance hidráulico e quantidade de violações de greide.

### 7.3 Ordem de síntese

**[PROPOSTA]** O cenário ESD será sintetizado nesta ordem:

1. fechar bacia contribuinte e condição de jusante;
2. validar MDT, breaklines, talvegues, estruturas e drenagem existente;
3. executar PCE/PCX preliminares;
4. selecionar receptores e dimensionar CEV ou estruturas necessárias;
5. resolver a drenagem de estradas e carreadores;
6. decompor o relevo em faces hidráulicas;
7. gerar linhas de controle e todas as linhas físicas de sulcação;
8. acoplar superfície 2D e rede 1D e recalcular o evento;
9. rejeitar concentrações, capacidades ou saídas inadequadas;
10. otimizar tiros e manobras somente entre alternativas hidraulicamente válidas.

## 8. Geração e otimização da sulcação

### 8.1 Campo de direção

Para uma superfície de elevação \(z(x,y)\), considere o gradiente \(g=\nabla z\) e a tangente à curva de nível \(t=(-g_y,g_x)\). Uma direção local unitária \(d\) apresenta declividade longitudinal aproximada \(s=g\cdot d\).

**[PROPOSTA]** A linha deve satisfazer, por segmento, limites de greide, curvatura, obstáculos e conexão hidráulica. Uma função objetivo inicial é:

\[
J = w_s\int(s-s_{alvo})^2\,dl
  + w_k\int\kappa^2\,dl
  + w_h\int(\theta-\theta_{bloco})^2\,dl
  + P_{travessias}+P_{morredores}+P_{falhas}+P_{sobreposicao}+P_{movimento}
\]

Os termos equilibram controle hidráulico, dirigibilidade, orientação geral, interrupções internas, skips, overlaps e movimentação de terra.

### 8.2 Algoritmo proposto

1. Erodir o polígono pela largura de cabeceira e pelas exclusões.
2. Decompor polígonos côncavos e vazados em células analíticas conectadas, preservando a continuidade através das fronteiras internas dessa decomposição.
3. Gerar linhas de controle candidatas: cordas retas, sementes de contorno, alinhamentos existentes e linhas relacionadas a terraços ou receptores verificados, sem presumir que a linha de controle seja um canal físico.
4. Rotear em grade ou navmesh com A* dependente de posição e direção, ou fast marching anisotrópico.
5. Suavizar por B-spline ou clotoide com otimização quadrática/sequencial.
6. Gerar linhas adjacentes por isolinhas de um campo escalar alinhado ao terreno, evitando offsets repetidos que geram auto-interseções e variação de espaçamento.
7. Recortar somente em superfícies físicas classificadas; costurar as células analíticas e reprovar endpoints internos sem suporte, cruzamentos, sobreposições e loops.
8. Amostrar perfis 3D e validar greide, curvatura, rampa transversal, quebra vertical e clearance.
9. Calcular segmentos de implemento e alcances hidráulicos.
10. Somente após a geometria ser válida, otimizar ordem e rota operacional.

O contrato de frota mantém dois raios independentes. `fleet.minimum_work_path_radius_m` limita a curvatura da trajetória com o implemento trabalhando; `fleet.minimum_turn_radius_m` limita giros, retornos e manobras em cabeceiras ou corredores. Um não pode ser usado como substituto do outro. Quando o primeiro não estiver disponível, a checagem de raio da linha recebe `NOT_EVALUATED_MISSING_STATIC_PATH_RADIUS_REQUIREMENT`; esse é o estado do pacote E0 atual e não constitui aprovação de dirigibilidade.

**[BASE TÉCNICA]** A literatura de planejamento agrícola já oferece decomposição de talhões, geração de swaths, rotas e caminhos, além de otimização de linhas com curvatura contínua. Essas técnicas são úteis para mecanização, mas não resolvem o dimensionamento hidráulico. [Fields2Cover](https://arxiv.org/abs/2210.07838) e [Continuous-curvature agricultural wayline generation](https://www.sciencedirect.com/science/article/pii/S1537511023002623)

## 9. Tiros longos e travessias entre talhões

### 9.1 Grafo de zonas operáveis e portais

As zonas operacionais ou talhões separados por uma superfície física são nós de um grafo operacional, sempre identificados por `farm_id`, `legal_land_unit_id` e `field_id`. As arestas não são simples proximidades geométricas: são `portals` classificados, com corredor 3D, perfil, drenagem, limite atravessado, estado do implemento e permissões.

Uma divisa entre polígonos que represente apenas uma subdivisão cadastral ou de gestão, dentro do mesmo escopo autorizado, não se torna portal quando não há carreador, obstáculo ou faixa física no terreno. Nesse caso, o futuro solver deve dissolver a divisa e resolver um único bloco operacional contínuo, mantendo `field_id` apenas para rastreabilidade e métricas. Criar um `movement_segment` sobre essa divisa inventaria uma parada inexistente. Limites fundiários, permissões ou condições físicas desconhecidos permanecem fechados, conforme os gates abaixo.

Uma conexão candidata precisa possuir corredor levantado e será rejeitada se faltar:

- permissão de operação e propriedade;
- compatibilidade de espaçamento e tráfego controlado;
- levantamento de estrada, valeta, terraço, CEV ou bueiro atravessado;
- envelope de largura, raio, rampa, rolagem, quebra vertical e clearance;
- solução para o fluxo interceptado e para o destino da água;
- proteção de APP, áreas úmidas, infraestrutura e terceiros.

O limite desconhecido permanece fechado. Estar na mesma nuvem de pontos demonstra apenas cobertura topográfica; não demonstra direito de trânsito, trabalho, movimentação de terra ou transferência de água.

### 9.2 Como formar tiros entre talhões

O motor testará duas famílias de conexão:

1. `PHASE_LOCKED`: resolve um campo de fase global `phi` sobre os talhões e os gaps levantados, extrai `phi = delta + k * espacamento` e recorta cada linha nos polígonos cultiváveis. O mesmo `k` se torna `row_global_id` nos dois lados; `delta` é otimizado para reduzir sobras e aumentar pares válidos.
2. `PORT_MATCHING`: mantém em cada talhão a direção conservacionista local, transforma cada endpoint em uma porta com XYZ, tangente, sentido, fase e índice e executa pareamento de custo mínimo. A ordem lateral precisa ser monotônica para impedir conectores cruzados.

Uma conexão aceita vira a cadeia:

```text
WORK(talhao A) -> LIFT -> CROSS(portal) -> LIFT/ALIGN -> WORK(talhao B)
```

O comprimento do tiro operacional soma os trechos `WORK`, mas informa separadamente o span e as distâncias `LIFT/CROSS`. A economia estimada de uma ligação é a diferença entre a manobra isolada e o custo de levantar, atravessar e readquirir a próxima linha. Tempos e velocidades vêm da frota e da telemetria da fazenda, não de constantes universais.

```text
beneficio_portal = tempo_modo_isolado
                 - (tempo_lift_saida + tempo_cross + tempo_reaquisicao)
```

O portal só participa do Pareto quando `beneficio_portal > 0` e todos os gates fundiários, mecânicos, agronômicos e hidráulicos forem atendidos. O motor também mede combustível, compactação, pisoteio, área perdida e risco, porque tempo sozinho pode favorecer uma travessia inadequada.

Se as famílias forem perpendiculares ou incompatíveis em fase, não existe tiro direto. Pode existir uma rota conectada com transição de curvatura contínua, desde que caiba no portal e no envelope varrido da colhedora e dos transbordos. O otimizador deve comparar essa rota com um eixo comum de compromisso e com a manutenção das duas famílias locais.

### 9.3 Travessia de terraço passante

**[PROPOSTA]** Uma seção de base larga não será automaticamente marcada como passante. A travessia será um `crossing_node` aprovado, contendo geometria, ângulo, estação, perfil, cota mínima de crista, envelope da máquina, estado do implemento e cenário de compactação.

No crossing, o motor deve simular a trilha de roda e uma tolerância de entalhe na crista. Quando o sulco contínuo puder cortar a seção, a linha guia continuará, mas o sistema criará um intervalo `LIFT` para interromper a continuidade hidráulica. O projeto exportado precisa carregar essa diferença; uma polilinha única sem estados de implemento é ambígua e insegura.

### 9.4 Modos operacionais comparáveis

Cada macrocenário conservacionista elegível será cruzado com quatro modos, sem transformar preferência operacional em método de conservação:

| ID | Modo | Semântica |
|---|---|---|
| `OC0_ISOLADO` | `ISOLADO_POR_TALHAO` | família e rota locais, baseline obrigatório |
| `OC1_EIXO_COMUM` | `EIXO_COMUM` | mesma orientação/fase, sem afirmar travessia |
| `OC2_GUIA_CONTINUA` | `GUIA_CONTINUA` | guia única, com `LIFT/CROSS` no portal |
| `OC3_TRABALHO_CONTINUO` | `TRABALHO_CONTINUO` | implemento engatado somente em portal cultivável e aprovado |

“Pular o carreador” será normalmente `OC2`. Em curva embutida, o sulco físico termina na estrutura salvo passagem projetada. Em base larga/passante, `OC3` só existe se a seção nominal e degradada conservar capacidade e se o perfil 3D for trafegável. No ESD, a guia pode ser longa, mas cada alcance de água mantém greide, contribuição e receptor próprios.

Entre fazendas adjacentes, aplicam-se os mesmos modos com autorização por escopo (`TRANSIT`, `WORK`, `EARTHWORK`, `WATER_TRANSFER`), compatibilidade de safra, operador, frota e espaçamento. Fazendas não adjacentes ligadas por estrada pertencem ao roteamento logístico, não a um único sulco ou tiro de trabalho.

### 9.5 Critério de aceitação

Um tiro entre talhões somente será aceito quando:

- todos os seus segmentos forem mecanizáveis;
- todo obstáculo tiver travessia explícita;
- nenhum crossing reduzir a capacidade abaixo da margem aprovada;
- cada alcance hidráulico terminar em interceptação ou outlet seguro;
- a união não transferir água para estrada, APP, vizinho ou estrutura subdimensionada;
- a economia operacional não depender de violar uma restrição hidráulica dura.

**[BASE TÉCNICA]** A literatura denomina a ligação de linhas topograficamente conectáveis através de carreadores como `direct shot` ou `long shot`. No caso estudado por Santoro, Soler e Cherri, o otimizador reduziu 31,64% do tempo de manobras; esse valor é evidência de potencial no caso, não uma promessa ou parâmetro do produto. [Santoro, Soler e Cherri, 2017](https://repositorio.unesp.br/server/api/core/bitstreams/c67d5b0e-cd67-48b8-9fec-02d2461d0d23/content)

### 9.6 Rede elétrica e outras barreiras

Na V1, uma rede elétrica informada pelo cliente é um `POWER_LINE_AXIS`: `LineString` 2D traçado pelo centro dos postes. O eixo recebe uma faixa de exclusão cuja largura possui fonte e revisão; não existe afastamento universal embutido no motor.

Essa faixa é barreira absoluta. O gerador subtrai seu polígono da área operável antes de extrair a família, divide qualquer `worked_segment` e `hydraulic_reach` interceptado e proíbe `LIFT_CROSS`, `WORK_THROUGH`, portal, POA, fila, manobra, carregamento e descarga. Dois trechos colineares em lados opostos continuam podendo compartilhar `guidance_group_id`, mas são tiros e rotas distintos.

```text
WORK -> WORK_END:POWER_BARRIER
     -> rota termina ou desvia

outro lado -> WORK_START:POWER_BARRIER -> WORK
```

O catálogo geral de interferências precisa declarar `COMPLETE`, `PARTIAL` ou `NOT_REVIEWED`; o inventário elétrico declara `PROVIDED`, `DECLARED_NONE` ou `NOT_REVIEWED`. Sem shape e sem `DECLARED_NONE`, conexões e POAs ficam pendentes. A nuvem atual contém somente pontos classificados como solo e não comprova ausência de postes ou cabos.

Ferrovias, vias públicas, dutos, cercas, pivôs, cursos d'água, APPs, estruturas hidráulicas, árvores, rochas, erosões e áreas úmidas usam a mesma arquitetura, mas cada classe define efeitos próprios por plantio, colheita, trânsito, movimentação de terra, hidráulica e POA.

## 10. Carteira de macrocenários

### 10.1 Presets apresentados ao cliente

| ID | Macrocenário obrigatório | Conteúdo fixo | Variantes calculadas |
|---|---|---|---|
| `C1_CURVA_EMBUTIDA` | Curva embutida | seção embutida e faixas de sulcação entre terraços | `EMBUTIDA_TI` e `EMBUTIDA_TD` |
| `C2_BASE_LARGA_PASSANTE` | Base larga/passante | seção de base larga e intenção de travessia mecanizada | `BASE_LARGA_TI_PASSANTE` e `BASE_LARGA_TD_PASSANTE` |
| `C3_ESD` | ESD | sulcos participam da rede de escoamento distribuído | `ESD_SETORIAL` e `ESD_COMPLEMENTADO` |
| `C4_MISTO_POR_ZONA` | Misto por zonas | atribuição espacial dos três métodos | combinações elegíveis com interfaces calculadas |

Os três primeiros sempre participam da rodada. Quando um deles não atender aos gates, o resultado será `NAO_APLICAVEL`, com os dados, regras e violações que causaram o bloqueio. O motor não criará uma solução insegura apenas para preencher a comparação.

### 10.2 Fatoração técnica interna

A taxonomia fixa S0-S6 foi abandonada porque misturava referências, sistemas físicos, seções construtivas e preferências operacionais. Um cenário passa a ser composto explicitamente por dimensões independentes:

| Dimensão | Valores iniciais |
|---|---|
| Macrocenário do cliente | `CURVA_EMBUTIDA`, `BASE_LARGA_PASSANTE`, `ESD`, `MISTO_POR_ZONA` |
| Referência | `NENHUMA`, `EXISTENTE`, `MANUAL_IMPORTADO` |
| Sistema conservacionista | `RETENCAO_TERRACEADA`, `DRENAGEM_CONTROLADA`, `ESD_DISTRIBUIDO`, `MISTO_POR_ZONA` |
| Geometria da sulcação | `PROXIMA_CONTORNO`, `GREIDE_CONTROLADO`, `RETAS_ELEGIVEIS`, `CAMPO_DIRECIONAL_SUAVE` |
| Intervenção | `MANTER_INFRA`, `ADAPTACAO_LOCAL`, `REDESENHO_INTEGRADO` |
| Conexão operacional | `ISOLADO_POR_TALHAO`, `EIXO_COMUM`, `GUIA_CONTINUA`, `TRABALHO_CONTINUO` |
| Frota | conjunto versionado de sulcador, plantadora, colhedora e transbordos |
| Incerteza | conjunto versionado de terreno, chuva, solo, rugosidade, obstrução e execução |

O macrocenário é um envelope compreensível para comparação. Dentro dele, seção, função hidráulica, controle longitudinal e operabilidade continuam independentes. CEV é componente da rede quando necessário. “Tiros longos” é preferência da otimização. Projeto existente e projeto manual são baselines de comparação.

Todos os candidatos devem usar o mesmo terreno, solo, chuva, frota, restrições e `rule_pack` dentro de uma rodada comparativa. Uma preferência operacional nunca pode desligar uma restrição de segurança.

Dentro de cada macrocenário elegível serão mantidos representantes de conservação, equilíbrio e operação. O comparativo principal mostrará o melhor representante de cada macrocenário e o misto por zonas quando aplicável; as demais soluções não dominadas permanecerão como alternativas técnicas. Uma família inelegível aparecerá como não aplicável, com os motivos.

### 10.3 Métricas comuns

Métricas hidráulicas:

- volume, pico, lâmina, velocidade, tensão e margem de capacidade por elemento;
- área contribuinte e alcance hidráulico máximo;
- descarga protegida e não protegida;
- armazenamento e tempo de esvaziamento;
- capacidade e bordo livre do CEV;
- direção e impacto de extravasamento/falha;
- concentração da descarga entre sulcos;
- alteração relativa do risco de erosão.

Métricas operacionais e econômicas:

- área plantável e área perturbada;
- distribuição P10/P50/P90 e média dos segmentos trabalhados;
- comprimento das linhas guia e dos alcances hidráulicos;
- endpoints internos ou morredores;
- manobras por tipo, área de cabeceira e percurso total;
- comprimento físico trabalhado, comprimento do tiro operacional e span total;
- proporção `WORK / (WORK + LIFT + CROSS)`;
- `LIFT`, `CROSS` e portais por tipo, incluindo pendências de autorização;
- maior guia multi-talhão e economia frente ao baseline isolado;
- skips, overlaps e violações de espaçamento;
- travessias, raio, rampa, rolagem e quebras verticais;
- corte, aterro, comprimento de obras e custo parametrizado;
- tempo, consumo e capacidade operacional como proxies explicitamente identificados.

### 10.4 Fronteira de Pareto

**[PROPOSTA]** O produto apresentará alternativas não dominadas em quatro eixos separados: conservação e robustez; colheitabilidade; capacidade operacional; intervenção e área útil. A decisão final não será uma nota única. Cada cenário mostrará restrições duras, sensibilidades e hipóteses de custo separadamente.

Os presets canônicos estão em [cenarios_conservacionistas.json](../config/cenarios_conservacionistas.json), e as instâncias serão validadas por [conservation-scenario.schema.json](../schemas/conservation-scenario.schema.json).

## 11. Comparação controlada com projeto AgroCAD

O AgroCAD declara suporte a linhas para piloto automático, alternativas de plantio, limites, obstáculos, terreno de VANT, terraços, drenagem e plantio com greide controlado. Isso o torna uma referência prática útil, não um padrão de verdade hidráulica. [AgroCAD, página oficial do produto](https://www.agrocad.com.br/?page_id=142)

### 11.1 Protocolo

1. Congelar DTM, CRS, contornos, vazios, solos, chuva, máquinas, regras e briefing em um pacote versionado.
2. Solicitar que um projetista experiente produza o projeto AgroCAD sem acesso prévio à solução automática.
3. Receber DWG nativo, DXF com dicionário de camadas, LandXML quando disponível, SHP/KML/CSV, relatórios, parâmetros e decisões manuais.
4. Gerar a carteira automática sem acesso prévio ao resultado manual.
5. Importar o manual como baseline e normalizar todas as geometrias para o mesmo CRS, DTM e passo de amostragem.
6. Comparar geometria, operação, terraplenagem e hidráulica com o mesmo motor.
7. Realizar revisão cega por profissionais e registrar preferências e justificativas.
8. Executar simulação de controlador e, em piloto, locação/as built e inspeção após chuvas.

### 11.2 Comparação geométrica e operacional

Linhas não devem ser pareadas apenas pelo número ou ordem. O comparador deverá usar sobreposição de cobertura, diferença do campo angular e pareamento bipartido, seguido de Hausdorff ou Fréchet. Medir também espaçamento, skips, overlaps, endpoints, comprimentos, turns, cabeceiras, greide, curvatura, crossings, área perdida e volumes.

### 11.3 Comparação hidráulica

As duas soluções devem ser queimadas na mesma superfície base e submetidas à mesma chuva, solo e incerteza. Comparar contribuição, Q, volume, velocidade, tensão, armazenamento, CEV, outlets e direção de falha. Repetir os testes de assoreamento, obstrução, tolerância construtiva, condição antecedente e vegetação não estabelecida.

### 11.4 Aceitação

**[PROPOSTA]** O critério não será reproduzir o desenho do AgroCAD. A solução candidata deve apresentar:

- zero violação dura não resolvida;
- desempenho hidráulico não inferior no envelope de incerteza aprovado;
- desempenho operacional comparável ou melhor;
- desvios em relação ao manual explicáveis e reproduzíveis;
- aprovação profissional e evidência de campo no piloto.

Os limiares numéricos serão definidos com especialistas e dados do piloto. Inventar tolerâncias antes dessa calibração criaria falsa objetividade.

## 12. Outputs e interoperabilidade

### 12.1 Formatos canônicos

- PostGIS para entidades, redes, topologia, revisões e auditoria.
- GeoPackage como pacote vetorial portátil, preservando tabelas e relações.
- `LineStringZ` para alinhamentos, nunca somente geometria 2D no projeto técnico.
- COG/GeoTIFF para DTM, superfície proposta, risco, acumulação, profundidade e velocidade.
- TIN e LandXML para superfícies, alinhamentos, perfis e seções em CAD.
- GeoParquet para resultados volumosos e métricas analíticas.
- DXF/DWG em camadas padronizadas para Civil 3D e AgroCAD.
- CSV para estaqueamento, perfil e memória tabular.
- GeoJSON apenas para visualização web; SHP e KML como exportações de compatibilidade com perda declarada.

### 12.2 Pacote de entrega

Cada exportação deve conter:

```text
manifest.json
inputs/
terrain/
constraints/
structures/
guidance/
hydraulics/
logistics/
metrics/
reports/
qa/
approvals/
```

O manifesto informa hashes, CRS, datum, unidades, versões do DTM, cenário, `rule_pack`, software, parâmetros, data, responsável e limitações. Arquivos de máquina serão adaptadores derivados, nunca o armazenamento mestre.

**[PROPOSTA]** Uma exportação de linhas para controlador deve preservar `guidance_line`, `worked_segment`, `movement_segment` e comandos de implemento. Quando o formato do fabricante não suportar essa semântica, o sistema deverá gerar arquivos separados e relatório de limitações, em vez de condensar tudo em uma polilinha enganosa.

## 13. Gates de qualidade e liberação

| Gate | Evidência necessária | Falha bloqueia |
|---|---|---|
| G0 — Referência | CRS, unidade e datum horizontal/vertical confirmados | Cálculo de greide e integração |
| G1 — Superfície | Checkpoints independentes, incerteza e 100% da área útil dentro do footprint do DTM e sobre células válidas | Geração de linhas e projeto executivo |
| G2 — Bacia | Montante/jusante, entradas e saídas de borda conhecidos | PCX e dimensionamento |
| G3 — Feições | Vazios, estrada, vala, APP, bueiro, obstáculo e limite classificados | Geração automática segura |
| G4 — Agronomia | Solo, infiltração, erodibilidade, cobertura e manejo | Tipo e espaçamento executivos |
| G5 — Chuva | Fonte, IDF/hietograma, duração e TR aprovados | Capacidade hidráulica |
| G6 — Outlets | Vistoria, cotas, estabilidade e capacidade a jusante | Drenagem, CEV e ESD |
| G7 — Máquina | Perfil, implemento, raio, cabeceira e crossings | Linhas de máquina |
| G8 — Modelo | Conservação de massa, convergência, sensibilidade e QA | Publicação do cenário |
| G9 — Revisão | Parecer, exceções, vistoria e responsabilidade registradas | Liberação executiva |
| G10 — Campo | Locação, as built e inspeção pós-chuva | Calibração e replicação |
| G11 — Portais e direitos | Limites fundiários, corredor, perfil, drenagem e permissões por uso | Continuidade multi-talhão ou multifazenda |
| G12 — Interferências | Declaração completa, efeitos por operação e áreas de exclusão | Linhas, conexões e POAs |
| G13 — Energia | `POWER_LINE_AXIS` e buffer aplicados, ou `DECLARED_NONE` válido | Sulcação/rota/POA operacional |
| G14 — Frota do ciclo | Sulcador, plantadora, colhedora, transbordos, caminhões e obra | Colheitabilidade e logística |
| G15 — POA | Local, acesso, solo, drenagem, energia, giro e permissões | Implantação e uso do pátio |
| G16 — Capacidade logística | Massa, frota, baias, filas, entrega e contingência | Plano de colheita/CTT |
| G17 — Viabilidade | Nenhum candidato reprovado usado como fallback em E1+ | Publicação de cenário operacional |

Estados de saída:

- `TRIAGEM` = cenário `E0`: diagnóstico visual/geométrico e lacunas; sem cotas ou seções executivas.
- `ANTEPROJETO` = `E1` ou `E2`: cenários e métricas condicionados; marca “não liberar para máquina”.
- `PROJETO_TECNICO` = candidato `E3`: dados e cálculos completos; ainda requer revisão e aprovação.
- `LIBERADO` = `E3` aprovado: versão assinada, exportada e imutável.
- `AS_BUILT`: execução levantada e comparada com o `E3` liberado.

O gate de cobertura do DTM é binário para a geração: qualquer parcela da área útil fora do footprint ou sobre célula inválida/NoData bloqueia o cenário, em vez de permitir interpolação ou clamp silencioso.

## 14. Aplicação a um conjunto de referência

Em um conjunto local de referência, o pacote gera seis primitivas de comparação topográfica E0 por talhão inteiro (`E0A` a `E0F`); a decomposição zonal `E0H` não integra mais o gerador publicável. Essas primitivas permitem comparar campos de direção, linhas candidatas e comprimentos preliminares, mas não constituem decomposição operacional, rota, manobra ou solução conservacionista.

O verificador exige que todo segmento tenha endpoints classificados, sem endpoints internos sem suporte, cruzamentos, sobreposições ou loops. Isso comprova apenas o gate geométrico de continuidade E0; não comprova raio, envelope de frota, superfície de manobra nem autorização para máquina. O raio mínimo da trajetória em trabalho permanece `NOT_EVALUATED` no E0.

A triagem local de conexão mostrou por que orientação, fase e superfície operacional precisam ser tratadas separadamente. Em `E0F_EIXO_COMUM`, `shared_global_row_count` conta identidades globais presentes nos talhões antes dos gates e não representa pares válidos. Cada trecho preserva `phase_scope=GLOBAL_SHARED_STRAIGHT_LATTICE`, `row_global_id` e `phase_level_m = row_global_id * row_spacing_m`; somente depois entram pareamento de endpoints, cobertura geométrica do gap e limites de greide.

O `E0G_PORTAL_NORMAL_PHASE_LOCKED` faz uma varredura parametrizável de orientações em torno da normal local e de fases globais. Ele prioriza pareabilidade geométrica e não representa ótimo global de conservação, colheitabilidade ou desempenho.

Todos os pares `E0F` e `E0G` permanecem `UNCONFIRMED`: identidade global de fileira não comprova carreador, cabeceira, corredor, drenagem, permissão ou capacidade da frota. Sem superfície operacional classificada, esses candidatos não autorizam `connector`, `LIFT/CROSS` nem parada. Se a vistoria confirmar apenas uma subdivisão da mesma área operável autorizada, o solver pode dissolvê-la e gerar um bloco contínuo; se houver uma feição física, ela deve ser classificada e passar pelos gates de portal. Nenhum desses resultados é orientação de máquina.

Permanecem relevantes para o motor:

- 138 vazios internos que precisam ser classificados;
- possível contribuição externa em grande parte da área analisada;
- sete travessias de fluxo detectadas na borda;
- divisa central sem carreador ou superfície operacional classificada; até a vistoria, ela não é conector e não pode criar endpoint ou manobra;
- nenhuma declaração ou camada `POWER_LINE_AXIS`; o LAZ somente de solo não preserva postes/cabos;
- ausência de datum e checkpoints verticais independentes;
- falta de solo, infiltração, chuva, estruturas, outlets e perfil de máquina;
- ausência de produtividade, rede dirigível, frota CTT e locais de POA para análise logística;
- inexistência, no workspace, de um projeto AgroCAD vetorial para benchmark.

**[BLOQUEIO]** A diferença aproximada de 0,198 m observada entre duas superfícies derivadas da mesma fonte não representa acurácia absoluta. Ela é da mesma ordem de grandeza de greides controlados em trechos curtos. O dataset pode alimentar protótipos marcados como anteprojeto, mas não o dimensionamento e a locação de terraços, CEV, ESD ou sulcos executivos.

## 15. Esforço indicativo

Estimativas abaixo são **[PROPOSTA]** de planejamento, em pessoas-semana, e não orçamento comercial. Pressupõem acesso a especialistas, dados de teste e componentes geoespaciais já definidos na arquitetura geral.

| Entrega | Esforço indicativo | Resultado |
|---|---:|---|
| Domínio, contratos e QA do terreno | 3-5 | Entidades, gates, perfis e incerteza |
| Gerador geométrico de sulcação | 6-10 | Linhas, células, cabeceiras e métricas operacionais |
| Terraços e CEV candidatos | 8-12 | Corredores, seções, rede e volumes preliminares |
| Hidrologia acoplada e ESD | 10-16 | Sulcos como rede, eventos, capacidade e falhas |
| Otimização multicritério | 8-14 | MILP/CP-SAT, refinamento e Pareto |
| Importador/comparador AgroCAD | 4-7 | Normalização, pareamento e relatório de benchmark |
| Validação de campo e calibração | 8-12 semanas corridas | Controller, locação, as built e pós-chuva |
| Hardening, auditoria e exports | 8-12 | Execução reprodutível e pacotes versionados |

O desenvolvimento pode ser paralelizado, mas a primeira versão tecnicamente defensável continua condicionada ao ciclo de campo. O maior risco é a qualidade e completude dos insumos, não o tempo de CPU.

## 16. Decisões de implementação

1. Água é calculada antes e depois da mecanização proposta.
2. Linha guia, segmento trabalhado e alcance hidráulico são entidades distintas.
3. Terraços e canais formam uma rede com destino explícito; não são polilinhas isoladas.
4. ESD só existe com PCE, PCX, drenagem de estradas coerente e receptor estável para todo alcance; CEV e TD são condicionais ao cálculo e à variante regional.
5. Um tiro longo pode ser operacionalmente contínuo e hidraulicamente segmentado.
6. Terraço passante exige crossing projetado e verificado.
7. Espaçamento agronômico gera candidatos; capacidade hidráulica decide viabilidade.
8. Regras regionais e parâmetros são versionados e aprovados, não embutidos como constantes universais.
9. A comparação AgroCAD mede segurança, desempenho e explicabilidade, não semelhança visual.
10. Nenhuma saída para máquina é liberada enquanto houver gate obrigatório aberto.
11. Na V1, `POWER_LINE_AXIS` é barreira absoluta; não existe travessia automática sob a rede.
12. POA, rotas e capacidade são otimizados junto da sulcação e nunca adicionados apenas ao final.

## 17. POA e logística integrada

Depois que uma família passa pelos gates conservacionistas e de interferências, o motor calcula a massa acumulada ao longo de cada tiro. Cada vez que a capacidade efetiva do transbordo é atingida, surge um `load_event`; ao seu redor, o sistema busca uma `swap_window` que não invada cultura, estrutura, inclinação crítica ou barreira.

O grafo logístico dirigido é:

```text
load_event -> linha de tráfego -> cabeceira -> portal -> carreador
           -> acesso do POA -> baia -> estrada -> destino
```

As distâncias são roteáveis, nunca euclidianas. A seleção usa localização capacitada de instalações para escolher pátios e atribuir demanda, roteamento para separar viagens vazias/carregadas e despacho/simulação de eventos discretos para medir filas, espera da colhedora e regularidade de entrega.

O POA é um polígono operacional com acessos, baias, fila, giro, superfície, drenagem e capacidade. A carteira compara `P0_POA_EXISTENTE`, `P1_SEM_NOVA_OBRA`, `P2_NOVOS_POAS` e `P3_INTEGRADO`. O último realimenta direção/fase dos sulcos, saídas e carreadores antes de recalcular água e tráfego.

Um tiro maior não é automaticamente melhor: ele pode reduzir manobras e, ao mesmo tempo, fazer o transbordo encher longe de uma saída segura, aumentar tráfego carregado e parar a colhedora. Por isso a unidade de otimização é `sulcação + sequência + transbordos + POAs + caminhões + conservação`.

A especificação completa está em [POA e logística de colheita](./POA_E_LOGISTICA_DE_COLHEITA.md). A página pública do LOC confirma o uso de produtividade, velocidades, tempos, capacidades e eficiências para otimizar pátios e alocação, mas não publica seu algoritmo proprietário; ela é referência da classe de problema, não implementação a reproduzir. [LOC AgroAbdo](https://agroabdo.com.br/loc)

## 18. Fontes técnicas

- [IAC — Boletim Técnico 216, manejo e conservação do solo e da água](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf). Base para PCE/PCX, tipos de terraço, espaçamento, capacidade, drenagem e CEV.
- [ANA — Manual do Programa Produtor de Água, volume 5](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5). Base oficial para abordagem integrada, ESD, CEV e implantação.
- [Franco — tese ESALQ/USP sobre funcionamento do terraceamento e ESD](https://www.teses.usp.br/teses/disponiveis/11/11140/tde-21012019-150102/publico/Alexandre_Puglisi_Barbosa_Franco_versao_revisada.pdf). Fonte primária para sulcos como condutos, riscos de convergência, alinhamentos detalhados e integração PCE/PCX.
- [Sparovek — sistematização e conservação do solo, STAB 2013](https://www.stab.org.br/palestra_sistematizacao_2013/03_gerd_sparovek_23.pdf). Evidência setorial da separação entre linha-base, sulcação, CEV e carreadores; não é norma de dimensionamento.
- [Embrapa — Manejo e conservação em cana-de-açúcar](https://www.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/cana-de-acucar/producao/correcao-e-adubacao/manejo-e-conservacao). Referência agronômica para tipos e aplicação de terraços em cana.
- [AgroCAD — descrição oficial do produto](https://www.agrocad.com.br/?page_id=142). Evidência das capacidades declaradas usadas para definir o benchmark manual.
- [Fields2Cover — planejamento modular de cobertura agrícola](https://arxiv.org/abs/2210.07838). Fonte científica para decomposição, swaths, rotas e caminhos.
- [Continuous-curvature agricultural wayline generation](https://www.sciencedirect.com/science/article/pii/S1537511023002623). Fonte científica para otimização de linhas agrícolas com curvatura contínua.

As fontes orientam critérios agronômicos, hidráulicos e algoritmos de referência. O modelo de domínio, o solver híbrido, a separação entre continuidades, a taxonomia fatorial, os gates e os formatos de pacote são propostas de arquitetura deste projeto e precisam passar por validação de especialistas, testes controlados e piloto de campo.
