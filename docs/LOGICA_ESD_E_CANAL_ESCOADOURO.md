# Lógica do ESD e do Canal Escoadouro

**Versão:** 1.1
**Data:** 20 de agosto de 2026
**Status:** especificação técnica para prototipagem, validação agronômica e hidráulica

## 1. Correção conceitual

**ESD significa Escoamento Superficial Difuso.** Ele é um sistema de conservação do solo e de controle da enxurrada, não uma linha isolada do desenho e não um sinônimo de Canal Escoadouro Vegetado (CEV).

No ESD empregado na canavicultura, o excesso de chuva é distribuído entre muitas linhas de plantio com greide controlado. A rugosidade dos sulcos reduz a velocidade e cada sulco conduz somente uma parcela da contribuição até uma saída segura. O termo `difuso` descreve essa distribuição em muitos caminhos rasos; não significa que a água permanecerá necessariamente como uma lâmina uniforme sobre toda a vertente.

O **CEV** é outra entidade: um corredor físico largo, raso, vegetado, de pequena declividade e leito estável, dimensionado para receber e conduzir vazões acumuladas. Ele pode receber contribuições de sulcos, terraços, estradas, carreadores e bueiros. O IAC descreve sua locação, cálculo da área contribuinte, vazão, velocidade admissível e seção hidráulica no [Boletim Técnico 216](https://www.iac.sp.gov.br/publicacoes/publicacoes/iacbt126.pdf).

Na fala de campo podem aparecer `canal escoador`, `canal escoadouro`, `CEV` ou nomes internos de camada. O cadastro deve normalizar o nome, mas classificar o objeto pela função e pela seção levantada, nunca apenas pelo rótulo regional.

Portanto, a leitura correta é:

```text
ESD = sistema completo
sulcos = rede distribuída de pequenos condutos
CEV = receptor/coletor físico, quando necessário
carreador = infraestrutura operacional com drenagem própria
linha-base = controle geométrico, sem função hidráulica presumida
```

## 2. O que as fontes sustentam

### 2.1 Base oficial e científica

O [Manual do Programa Produtor de Água, volume 5, da ANA](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5) enquadra o ESD no planejamento integrado do controle da erosão e da enxurrada. O projeto considera bacia, solos, relevo, hidrologia, hidráulica, alinhamentos de plantio, estradas, carreadores e receptores. O próprio rol de produtos do manual trata o CEV de forma condicional, `se houver`; logo, a exigência universal não é “todo ESD contém CEV”, e sim “toda descarga possui destino estável e verificado”.

Essa regra nacional de produto não revoga regras mais restritivas. No `rule_pack BR-SP-CANA-IAC216`, TD e o Sistema Sem Terraços (ST) somente podem descarregar em **CEV ou prado escoadouro previamente implantado e estabilizado**, conforme a sequência construtiva do [Boletim Técnico IAC 216](https://www.iac.sp.gov.br/publicacoes/publicacoes/iacbt126.pdf). Assim, um receptor natural estável pode satisfazer a regra nacional, mas não substitui silenciosamente o receptor exigido pelo pack paulista: qualquer equivalência precisa estar prevista no próprio pack e ser aprovada pelo responsável técnico.

A tese de Alexandre Franco, ESALQ/USP, registra que as linhas de cana funcionam como canais de condução, alerta para convergência em talvegues e descreve o ESD como alinhamentos detalhados de pequeno greide e longa distância, integrados ao PCE e ao PCX. Também registra o encaminhamento para canais escoadouros vegetados ou drenagens naturais. Essa é uma fonte primária importante, mas o próprio trabalho alerta para a falta de evidência experimental suficiente e para o uso de recomendações empíricas como receitas gerais. Consulte a [tese completa](https://www.teses.usp.br/teses/disponiveis/11/11140/tde-21012019-150102/publico/Alexandre_Puglisi_Barbosa_Franco_versao_revisada.pdf), especialmente as páginas 19 e 45-49.

### 2.2 Evidência de prática setorial

Apresentações técnicas da STAB mostram projetos nos quais `linha base para plantio`, linhas completas de sulcação, `canal escoadouro` e `carreadores` aparecem como camadas distintas. Isso ajuda a interpretar a prática de projeto, mas não transforma cores, larguras ou valores de um exemplo em norma. Referências: [Sparovek, 2013](https://www.stab.org.br/palestra_sistematizacao_2013/03_gerd_sparovek_23.pdf) e [Marchiori, 2018](https://stab.org.br/palestras_meca_2018/meca2018_marchiori.pdf).

Materiais de empresas do setor descrevem variantes nas quais terraços de infiltração são substituídos por sulcação contínua em greide e CEV implantados em talvegues. Esses materiais devem ser registrados como `pratica_setorial`, não como regra nacional: [AGinfo](https://www.aginfo.agr.br/escoamento-superficial-difuso) e [EAG](https://eag.agr.br/escoamento-superficial-difuso-esd/).

## 3. Objetos que não podem ser confundidos

| Objeto | Existe fisicamente? | Função principal | Pode conduzir água? |
|---|---|---|---|
| `esd_system` | como conjunto implantado | organizar o controle distribuído da enxurrada | por meio de seus componentes |
| `esd_zone` | como área de projeto | agrupar uma família coerente entre barreiras, vias e receptores | pela superfície e pelos sulcos contidos |
| `esd_control_line` | às vezes é apenas geometria | orientar ou controlar uma família de linhas | somente se uma função hidráulica for confirmada |
| `furrow_reach` | sim | plantar e conduzir pequena contribuição | sim, dentro da capacidade verificada |
| `cev_reach` | sim | receber e conduzir descarga acumulada | sim, com seção e cobertura dimensionadas; origem natural ou construída registrada |
| `natural_receiver` | sim | receber descarga em corredor natural protegido | somente após verificação ambiental e hidráulica |
| `carrier_surface` | sim | tráfego, acesso e logística | incidentalmente; não deve virar canal por omissão |
| `road_drainage` | sim | interceptar e descarregar água da via | sim, por saídas e estruturas projetadas |
| `guidance_line` | não necessariamente | navegação da máquina | não define continuidade hidráulica |

### 3.1 Regra para linhas importadas

Uma cor não identifica função. Toda linha importada de DXF, DWG, SHP, GeoPackage ou KML deve receber semântica explícita a partir de nome da camada, legenda, memorial, perfil ou confirmação do projetista.

```text
esd_control_line
  id, geometry: LineStringZ
  esd_zone_id, row_family_id
  control_role:
    ROW_ALIGNMENT | FLOW_DISTRIBUTION | PHYSICAL_CONVEYANCE |
    ROAD_DRAINAGE | DIVIDE | UNCONFIRMED
  hydraulic_role:
    NONE | FURROW | DISTRIBUTOR | COLLECTOR |
    OUTLET_LINK | UNCONFIRMED
  source: GENERATED | IMPORTED | SURVEYED | DIGITIZED
  source_layer, source_style
  spatial_relation: BETWEEN_CARRIERS | ON_CARRIER |
    CROSSES_CARRIER | IN_FIELD | UNCONFIRMED
  review_status, reviewer, evidence
```

`source_style` preserva a cor original para visualização, mas nunca participa do cálculo. Uma linha `UNCONFIRMED` pode ser mostrada e comparada; não pode dimensionar, receber ou descarregar vazão.

`esd_zone` não é sinônimo de talhão, bacia ou polígono útil. É uma unidade de projeto formada por uma família coerente de sulcação e por relações conhecidas com carreadores, talvegues e receptores. Um talhão pode conter várias zonas, e uma linha-guia operacional pode ligar zonas de talhões diferentes.

### 3.2 Interpretação dos prints recebidos

O primeiro print informa que rosa representa terraços, verde representa sulcação e amarelo representa talhões. No segundo, o usuário informa que amarelo pertence ao projeto ESD e aparece entre carreadores. Isso não basta para concluir se o amarelo é:

- uma linha-base para gerar a família verde;
- um alinhamento de distribuição;
- um canal físico;
- uma drenagem de carreador;
- ou somente uma convenção da empresa que elaborou o desenho.

Até existir camada original, legenda, memorial ou perfil transversal, essas linhas devem entrar como `esd_control_line(control_role=UNCONFIRMED, hydraulic_role=UNCONFIRMED)`. O objetivo do TerraFlux não será reproduzir a aparência do print, mas reconstruir e validar a função de cada componente.

## 4. Cadeia física da água no ESD

O modelo deve representar a seguinte cadeia:

```text
chuva
  -> infiltração, interceptação e excesso superficial
  -> escoamento entre linhas e microtopografia
  -> captura por muitos sulcos
  -> condução longitudinal em cada furrow_reach
  -> transferência controlada no fim do alcance
  -> receptor natural protegido ou estrutura dimensionada
  -> CEV/rede principal, quando o PCX exigir
  -> exutório e condição de jusante conhecidos
```

Não basta calcular a declividade da linha. É preciso calcular quanta área lateral contribui para cada sulco, o que ocorre quando ele transborda, para onde a água migra entre linhas e como as descargas simultâneas se acumulam no receptor.

### 4.1 Continuidade operacional e hidráulica

O sistema terá dois grafos diferentes:

- `G_operacional`: trajetórias, manobras, cabeceiras, carreadores, estados do implemento e tiros entre talhões;
- `G_hidraulico`: células contribuintes, sulcos ativos, transferências, coletores, CEV, saídas e caminhos de falha.

Uma linha-guia pode atravessar um carreador e continuar no talhão seguinte. O sulco físico, porém, termina na borda da via, salvo quando existir uma travessia hidráulica projetada. A plataforma pode preservar um tiro longo para o operador e, simultaneamente, dividir a água em vários alcances seguros.

## 5. Ordem correta do projeto

O algoritmo não deve começar desenhando linhas verdes dentro de cada polígono. A ordem proposta é:

1. **Fechar o contexto da microbacia:** incluir toda contribuição externa a montante e os receptores a jusante.
2. **Validar o terreno:** MDT de solo, breaklines, estradas, talvegues, divisores, bueiros, canais, terraços, depressões reais e datum vertical.
3. **Executar PCE e PCX preliminares:** localizar fontes, caminhos, volumes, picos, velocidades, erosão e destinos atuais.
4. **Definir receptores primeiro:** preservar drenagem natural apta e gerar CEV, controles de greide, dissipação ou outras estruturas onde o PCX ou o `rule_pack` exigir. No pack IAC/SP, CEV/prado para TD e ST deve existir e estar estabilizado antes da conexão.
5. **Resolver estradas e carreadores:** cada superfície compactada recebe rede e saídas próprias; nenhuma via vira receptor por conveniência gráfica.
6. **Particionar o relevo em faces hidráulicas:** usar divisores, talvegues, receptores, barreiras e transições; as faces não precisam coincidir com os talhões cadastrais.
7. **Gerar linhas de controle candidatas:** testar diferentes famílias por face, sem presumir uma única linha central ou um greide nacional fixo.
8. **Gerar todos os sulcos reais:** manter espaçamento, suavidade, raio de giro e perfis longitudinais controlados; offset ingênuo de uma única curva não é suficiente.
9. **Acoplar hidrologia e hidráulica:** atribuir contribuição lateral a cada sulco, rotear o evento e acumular as descargas nos receptores.
10. **Rejeitar falhas e recalcular:** modificar orientação, dividir alcances, incluir receptor ou mudar a família conservacionista.
11. **Otimizar operação:** somente entre alternativas que já respeitem as restrições duras de conservação e segurança.
12. **Revisar em campo e implantar por etapas:** estruturas receptoras são estabilizadas antes de receber as linhas que descarregam nelas.

## 6. Geração geométrica das linhas

### 6.1 Campo de direção

Para o MDT (z(x,y)), o gerador procura um campo de direção suave (d(x,y)). A declividade longitudinal local é aproximada por:

\[
s_l(x,y)=\nabla z(x,y)\cdot d(x,y)
\]

O campo deve equilibrar:

- intervalo de greide definido pelo `rule_pack` local;
- continuidade do sinal de escoamento e ausência de bolsões não intencionais;
- baixa convergência de descarga entre sulcos;
- raio mínimo, curvatura e quebra vertical aceitos pela frota;
- espaçamento e paralelismo operacional;
- conexão a receptores verificados;
- limites ambientais, vias, estruturas e obstáculos;
- comprimentos úteis, manobras e compactação.

Valores como 2% de greide, 5% de limite ou 30 m de raio aparecem em exemplos e referências específicas. Eles devem entrar como parâmetros rastreáveis de um `rule_pack`, nunca como constantes universais.

### 6.2 Família de sulcos

A linha de controle não é simplesmente copiada por offsets sucessivos. Em relevo variável, esse procedimento altera o greide, cria convergência, auto-interseção e espaçamentos irregulares. O gerador deve construir uma família completa a partir de um campo escalar ou de uma solução de cobertura, e otimizar cada linha ou grupo de linhas com dependência espacial.

Cada linha gerada será quebrada em `furrow_reach` nos pontos em que houver:

- carreador, estrada ou cabeceira;
- terraço ou canal;
- mudança de receptor;
- depressão ou reversão não tolerada;
- transição de face hidráulica;
- travessia projetada;
- limite de capacidade ou alcance.

## 7. Modelo hidrológico e hidráulico

### 7.1 Níveis de fidelidade hidráulica

Os códigos `H0-H3` classificam somente a fidelidade do modelo hidrológico e
hidráulico. Eles são independentes dos níveis de entrega `E0_TRIAGEM`,
`E1_OPERACIONAL`, `E2_CONSERVACIONISTA` e `E3_EXECUTIVO`. Atingir uma
fidelidade `H2` ou `H3` não autoriza, por si só, uma entrega de mesmo número:
todos os gates de evidência, operação, segurança, campo e aprovação do nível
de entrega continuam obrigatórios.

| Fidelidade | Método | Uso permitido |
|---|---|---|
| H0 | geometria 3D, perfis e conectividade topográfica | triagem de alinhamentos |
| H1 | chuva-excesso distribuída + roteamento simplificado | comparar cenários e encontrar concentrações |
| H2 | acoplamento 2D da superfície com rede 1D de sulcos/CEV | anteprojeto hidráulico |
| H3 | modelo calibrado, estruturas levantadas e validação de campo | suporte técnico ao projeto executivo profissional, sujeito aos gates de `E3_EXECUTIVO` |

Para H1, o excesso pode ser calculado por método explícito e versionado, como Green-Ampt ou CN, com análise de sensibilidade. Para H2, o escoamento superficial deve trocar massa com os sulcos e estes com CEV e receptores. Equações de onda cinemática ou difusiva e Manning podem representar os trechos 1D, desde que limitações e calibração sejam declaradas.

### 7.2 Cálculos por sulco

Cada `furrow_reach` precisa armazenar:

```text
furrow_reach
  geometry: LineStringZ
  upstream_node, downstream_node
  lateral_contributing_area
  hydraulic_length, grade_profile
  section_state_by_crop_stage
  roughness_state_by_crop_stage
  inflow_hydrograph, outflow_hydrograph
  depth, velocity, shear, freeboard_or_capacity_margin
  overflow_cells, failure_destination
```

A seção e a rugosidade mudam com preparo, plantio, chuvas, tráfego, assoreamento e desenvolvimento da cultura. Por isso um único coeficiente fixo não representa todo o ciclo da cana.

### 7.3 Cálculos do CEV

O CEV é dimensionado por trecho, pois área contribuinte e vazão aumentam a jusante. Devem ser verificados:

- ponto de início, estações de entrada e exutório final;
- área contribuinte total e contribuição externa;
- perfil longitudinal, seção e eventual remanso;
- vazão de projeto e hidrogramas simultâneos;
- vegetação, rugosidade e velocidade/tensão admissíveis;
- profundidade, bordo livre, erosão nas entradas e estabilidade do leito;
- controles de greide, dissipadores, travessias e bueiros;
- condição de falha e destino do extravasamento;
- implantação e estabilização antes das conexões.

O cadastro deve distinguir `origin=NATURAL` (prado, depressão ou drenagem natural que teve estabilidade hidráulica, cobertura e condição ambiental verificadas) de `origin=CONSTRUCTED` (canal implantado, com seção, proteção, execução e estabilização verificadas). A existência de um talvegue não prova a existência de um CEV natural apto.

O CEV não deve ser criado automaticamente em todo talvegue. APP, drenagem natural, áreas úmidas, nascentes, solos frágeis, terceiros e condição de jusante podem tornar o corredor inadequado ou exigir outra solução. **APP nunca é receptor automático**: uma descarga ou obra que a alcance permanece bloqueada até análise hidráulica, ambiental e legal específica, inclusive quanto a alternativa locacional e autorizações aplicáveis. A proteção dada pela [Lei 12.651/2012](https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2012/lei/l12651.htm) não transforma APP em infraestrutura de drenagem disponível.

## 8. Regras duras e regras regionais

### 8.1 Regras duras do produto

Estas regras são propostas de segurança do TerraFlux:

```text
ESD_END_001   todo alcance hidráulico termina em receptor estável e verificado
ESD_ROLE_001  nenhuma geometria UNCONFIRMED participa do cálculo hidráulico
ESD_CONC_001  nenhuma convergência não dimensionada em talvegue, via ou cabeceira
ESD_ROAD_001  carreador não é outlet sem drenagem e capacidade explicitamente projetadas
ESD_EXT_001   contribuição externa e condição de jusante precisam estar no modelo
ESD_FAIL_001  todo elemento informa caminho e consequência de exceder a capacidade
ESD_QA_001    projeto executivo é bloqueado sem controle altimétrico e vistoria
ESD_APP_001   APP não é receptor automático; descarga ou obra exige análise específica
```

### 8.2 Regras condicionais do `rule_pack`

Estas dependem de região, solo, chuva, método e responsável técnico:

- intervalo permitido de greide dos sulcos;
- tratamento de greide constante, crescente, variável ou transições;
- alcance hidráulico e área contribuinte máximos;
- velocidade e tensão admissíveis por estado de superfície;
- obrigatoriedade e localização de CEV;
- uso combinado de TD, CEV, faixas vegetadas e dissipação;
- critérios para áreas sem terraços;
- chuva de projeto, período de retorno e fatores de segurança;
- raio de curvatura, espaçamento, cabeceira e cruzamentos;
- proteção ambiental e exigências de licenciamento.

Uma variante setorial pode exigir CEV em todo talvegue natural. Outra pode aceitar drenagem natural protegida ou exigir TD e CEV onde o ESD isolado não atende. O pack IAC/SP exige CEV/prado previamente estabilizado para receber TD e para compor ST. O cenário deve declarar a variante, a origem natural ou construída do receptor e provar sua capacidade; combinações não serão criadas silenciosamente.

## 9. Cenários que o motor deve comparar

Nesta seção, `ESD_SETORIAL` e `ESD_COMPLEMENTADO` são variantes internas do macrocenário cliente `C3_ESD`. A carteira global compara também `C1_CURVA_EMBUTIDA`, `C2_BASE_LARGA_PASSANTE` e, quando necessário, `C4_MISTO_POR_ZONA`.

| Código | Família | Interpretação |
|---|---|---|
| `ESD_SETORIAL` | alinhamentos detalhados, sem terraços contínuos na área apta | prática setorial condicionada a PCE, PCX e receptores |
| `ESD_COMPLEMENTADO` | ESD mais TD, CEV, dissipação ou faixas em zonas necessárias | componentes explícitos e recalculados como rede única |
| `ST_NAO_ESD` | sistema sem terraços, mas sem estratégia difusa demonstrada | não pode receber o rótulo ESD |
| `TERRACEADO` | TI ou TD com sulcação compatível | alternativa de comparação, não referência visual |
| `MISTO_POR_ZONA` | famílias diferentes em faces com condições distintas | transições e receptores precisam ser dimensionados |

O motor deve produzir uma fronteira de alternativas seguras. Ganho de tiro, área plantável ou rendimento operacional não compensa violação hidráulica.

## 10. Métricas de um cenário ESD

### 10.1 Conservação e hidráulica

- balanço de massa do evento;
- pico, volume, profundidade, velocidade e tensão por alcance;
- área lateral contribuinte e comprimento hidráulico por sulco;
- quantidade de sulcos sobrecarregados;
- distribuição das descargas, incluindo Gini e participação dos 5% mais carregados;
- área e volume de transbordamento entre sulcos;
- descarga protegida e não protegida;
- carga acumulada e margem de capacidade dos receptores;
- erosão potencial em sulco, entressulco, entradas e saídas;
- caminho da falha até áreas produtivas, APP, vias, vizinhos e infraestrutura;
- sensibilidade a chuva, infiltração, rugosidade e assoreamento.

### 10.2 Agronomia e operação

- área efetivamente plantável;
- distribuição de comprimentos trabalhados e tiros operacionais;
- manobras, cabeceiras, morredores, skips e overlaps;
- curvatura, raio, rampa transversal e quebra vertical;
- cruzamentos de carreadores e estado do implemento;
- tráfego e compactação previstos;
- corte/aterro e faixa perturbada;
- robustez das linhas a pequenas variações de locação e do MDT.

## 11. Insumos necessários

### 11.1 O que polígono e LAZ permitem

Com talhões e uma nuvem LAZ classificada como solo é possível:

- auditar densidade, vazios, ruído, classes e referência espacial;
- produzir MDT preliminar, declividade, curvaturas e perfis;
- mapear divisores, talvegues, depressões e conectividade topográfica;
- decompor faces do relevo;
- gerar linhas candidatas e medir seus greides e raios;
- detectar onde o recorte toca a borda e a bacia está incompleta.

Isso é suficiente para uma entrega **`E0_TRIAGEM` com fidelidade hidráulica `H0`**, não para afirmar que um ESD funciona hidraulicamente.

### 11.2 O que ainda é obrigatório para dimensionar

- MDT cobrindo toda a bacia contribuinte e o receptor a jusante;
- datum vertical e checkpoints independentes;
- estradas, carreadores, bueiros, valetas, terraços, canais e breaklines;
- solos por horizonte, profundidade, permeabilidade, infiltração, erodibilidade e compactação;
- IDF, hietograma, duração, período de retorno e chuvas consecutivas;
- cobertura, palhada, estágio da cultura, preparo e rugosidade;
- drenagens naturais, nascentes, áreas úmidas, APP e restrições ambientais;
- seções e estabilidade dos receptores e exutórios;
- espaçamento, bitola, implementos, raio, manobra e tráfego;
- vistoria agronômica, hidráulica e ambiental.

Sem esses dados, a saída deve dizer `candidato topográfico`, e não `projeto ESD aprovado`.

## 12. Validação da solução

### 12.1 Testes computacionais

1. Balanço de massa em todos os eventos.
2. Comparação do MDT bruto e hidrocondicionado.
3. Sensibilidade a resolução, chuva, infiltração, rugosidade e greide.
4. Teste de falha de cada sulco, CEV, bueiro e saída.
5. Verificação de que nenhum alcance termina em nó morto.
6. Comparação do roteamento simplificado com modelo 2D em áreas críticas.
7. Robustez a deslocamentos planimétricos e altimétricos compatíveis com o levantamento.
8. Auditoria independente das memórias de cálculo e unidades.

### 12.2 Piloto de campo

1. Conferir MDT, talvegues, divisores e estruturas por RTK/checkpoints.
2. Levantar solo e infiltração em posições representativas e críticas.
3. Implantar primeiro receptor, drenagem de vias e controles necessários.
4. Locar um bloco piloto, registrar geometria `as built` e estado do solo.
5. Monitorar chuvas, pontos de concentração, erosão, sedimentação e funcionamento das saídas.
6. Recalibrar o modelo e repetir em mais de uma condição de solo e relevo.
7. Liberar ampliação somente após revisão do responsável técnico.

## 13. Decisões fechadas para o TerraFlux

1. ESD será tratado como sistema PCE/PCX, não como camada colorida.
2. Sulco, linha de controle, CEV, carreador e linha-guia serão entidades distintas.
3. Haverá grafos operacional e hidráulico separados.
4. Todo alcance terá receptor estável; CEV será exigido quando o PCX e o `rule_pack` determinarem, inclusive CEV/prado previamente estabilizado para TD e ST no pack IAC/SP.
5. O projeto começará por bacia, drenagem e receptores, não por otimização de tiros.
6. Todas as linhas físicas serão geradas e verificadas; não será validada apenas uma linha-base.
7. Cor de CAD será somente estilo. Função exige evidência semântica.
8. Valores publicados serão referências regionais rastreáveis, não constantes universais.
9. O LAZ atual permitirá testar geometria e topologia, mas não liberar ESD executivo.
10. A solução será validada por cálculo, sensibilidade, campo e responsável habilitado; semelhança com um desenho AgroCAD não é critério de correção.
11. APP, talvegue e curso d'água nunca serão promovidos automaticamente a receptor.
