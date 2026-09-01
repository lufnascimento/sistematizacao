# Matriz Agronômica de Conservação e Colheitabilidade

**Versão:** 1.1
**Data:** 20 de agosto de 2026
**Status:** auditoria de aderência e contrato técnico para geração e comparação de cenários; não é projeto executivo

## 1. Finalidade

Esta matriz transforma a pesquisa agronômica, hidráulica e operacional em regras verificáveis para o motor de sulcação. Sua função é impedir três erros:

1. otimizar rendimento antes de provar conservação e segurança;
2. transformar referência regional ou resultado experimental em limite nacional;
3. confundir desenho topográfico com projeto hidráulico, rota operacional ou autorização executiva.

A sequência invariável é:

```text
insumos e permissões
  -> gates de conservação, hidráulica, ambiente e segurança
  -> gates de colheitabilidade e capacidade de suporte
  -> simulação colhedora + transbordos + vias + POA
  -> Pareto de produtividade, custo, intervenção e robustez
  -> revisão profissional e campo
```

Nenhuma pontuação posterior compensa um gate reprovado.

## 2. Hierarquia das regras

| Camada | Exemplo | Efeito |
|---|---|---|
| `NATIONAL_PRODUCT_GATE` | todo alcance termina em receptor estável e verificado | bloqueia qualquer candidato inseguro |
| `REGIONAL_RULE_PACK` | IAC/SP exige CEV/prado estabilizado antes de TD e ST | acrescenta obrigação na jurisdição aplicável |
| `PRIMARY_EVIDENCE` | offtracking medido em conjunto articulado | fundamenta modelo, intervalo e teste; não vira constante universal |
| `SECTOR_PRACTICE` | variante ESD usada por empresa/usina | gera cenário sujeito a validação |
| `USER_APPROVED_PARAMETER` | raio, chuva, frota, tolerância e risco aceitos | parametriza o cálculo com autoria e versão |
| `OBJECTIVE` | tiros maiores, menos manobras, menor km/t | ranqueia apenas candidatos aprovados nos gates |

O relatório deve informar para cada regra: `rule_id`, versão, natureza, território, cultura, condição, resultado, fonte/página, parâmetros usados, evidência do cálculo, aprovador e eventual exceção.

## 3. Auditoria de aderência do produto

### 3.1 Legenda obrigatória

| Estado | Significado verificável |
|---|---|
| `IMPLEMENTADO` | existe código executável e contrato verificável para o recorte declarado; não amplia a alegação além desse recorte |
| `ESPECIFICADO` | regras, entradas, saídas e gates estão documentados, mas não existe ainda solver integrado e verificável para gerar o produto |
| `BLOQUEADO` | a execução ou liberação depende de insumo, calibração, método, integração, ensaio ou aprovação que não existe no pacote atual |

O estado não é uma nota média. Um mesmo domínio pode ter uma etapa geométrica `IMPLEMENTADO` e a liberação agronômica `BLOQUEADO`; por isso os recortes aparecem em linhas separadas. Configuração, schema ou validador de configuração provam contrato, não provam que o cenário foi calculado.

### 3.2 Matriz auditada

| Domínio e recorte | Estado | Evidência existente | Gap ou blocker | Parâmetros mínimos | Critério de aceite do recorte |
|---|---|---|---|---|---|
| CF0, família contínua geométrica | `IMPLEMENTADO` | [motor CF0](MOTOR_CF0_FAMILIA_CONTINUA.md), `generate_continuous_family.py`, `continuous_family.py`, verificador e pacote persistido | não resolve conservação, hidráulica, manobra articulada nem exportação de guia | MDT métrico, área operável, espaçamento, pesos do campo axial, tolerâncias de fase/spline, raio de trabalho quando fornecido | contrato CF0 1.2 válido; quatro camadas; linhas aceitas e diagnósticas separadas; topologia, espaçamento, greides e raio conservador rechecados; saída continua `HYDRAULIC_UNCONFIRMED` |
| CF0, liberação como sulcação | `BLOQUEADO` | o próprio contrato CF0 proíbe autorização de guidance e marca diagnósticos como não aprovados | faltam PCE/PCX, receptores, seções, frota/manobras, solo e validação de campo | todos os parâmetros dos gates C1-C3, frota real, permissões e evidência E2/E3 | somente após um solver C1, C2 ou C3 reproduzível aprovar todos os gates; CF0 isolado nunca satisfaz esse aceite |
| C1, curva embutida TI/TD | `ESPECIFICADO` | [produto analítico](PRODUTO_ANALITICO_DE_SULCACAO.md), [métodos regionais](METODOS_CONSERVACIONISTAS_REGIONAIS.md), catálogo e schema de cenários | não há solver C1 nem produto hidráulico calculado | PCE, PCX, chuva, solo, seção embutida, espaçamento, greide, bordo livre, MDT proposto, receptor, custo e tolerância construtiva | balanço de massa fechado; seção e espaçamento aprovados no envelope de eventos; receptor e caminho de falha verificados; colheitabilidade e implantação aprovadas |
| C2, base larga/passante TI/TD | `ESPECIFICADO` | contrato C2 e regra de passabilidade condicionada documentados | não há solver C2; “passante” não está provado para a frota nem para seção degradada | parâmetros C1 mais seção nova/degradada, recalque, trilhas de roda, envelope varrido, carga por eixo, umidade e nós de cruzamento | hidráulica aprovada nos estados novo e degradado; travessias explícitas; nenhuma unidade invade seção ou cultura; manutenção mantém capacidade e trafegabilidade |
| C3, ESD setorial/complementado | `ESPECIFICADO` | [lógica ESD](LOGICA_ESD_E_CANAL_ESCOADOURO.md), contrato C3 e regras de difusão | não há solver C3; nenhuma linha atual pode receber rótulo ESD | família completa, `hydraulic_reach`, contribuição externa, chuva-excesso, infiltração, greide, convergência, transferências, receptores e rule-pack CEV | cada alcance fecha contribuição, capacidade, destino e falha; concentração não dimensionada igual a zero; receptor aceito; indicadores de difusão e robustez dentro dos limites aprovados |
| PCE, controle de erosão | `ESPECIFICADO` | `PCE_001`, RUSLE limitada ao seu domínio e práticas conservacionistas documentadas | não existe motor PCE calibrado localmente; solo/cobertura/manejo não acompanham o pacote atual | erodibilidade, cobertura por fase, manejo, comprimento/rampa, evidência de erosão e calibração local | suscetibilidade/perda e concentração avaliadas com proveniência; práticas selecionadas e limitações declaradas; RUSLE nunca usada como PCX |
| PCX, controle de enxurrada | `BLOQUEADO` | contrato hidrológico/hidráulico e gates estão especificados | pacote atual não calcula chuva-excesso, hidrograma, armazenamento, seções ou falha | IDF e distribuição temporal, Tc, infiltração por estado, bacia completa, seções/rugosidade, sedimento, obstrução e jusante | balanço de massa reproduzível para eventos nominais e adversos; Q, nível, velocidade/tensão e bordo livre aprovados; sensibilidade e incerteza publicadas |
| Receptores e CEV | `ESPECIFICADO` | `RECEIVER_001`, `CEV_001`, classes `NATURAL`/`CONSTRUCTED` e pack IAC/SP | receptores não foram levantados, dimensionados nem aceitos em campo/as built | microbacia, seção, cobertura, estabilidade, descarga final, permissão, fase de estabilização, inspeção e manutenção | todos os alcances têm receptor rastreável; CEV construído está estabilizado e aceito antes da conexão quando exigido; caminho de excedência não cria dano intolerável |
| QA topográfico e produtos do LAZ | `IMPLEMENTADO` | inventário da nuvem, densidade, MDT, declividade, curvas e fluxo topográfico em `dataset/derived` | produtos são E0; classificação de solo, vazios e bordas ainda exige revisão | CRS, datum declarado, classificação, densidade/cobertura, resolução e máscaras | arquivos reproduzíveis, métricas de QA persistidas, 100% da área útil coberta por células válidas e limitações E0 explícitas |
| Incerteza do MDT para E2/E3 | `BLOQUEADO` | contrato exige checkpoints independentes e mapa de resíduos | não há evidência suficiente de acurácia absoluta, datum vertical e propagação da incerteza às quedas pequenas | checkpoints independentes distribuídos, método, datum/geóide, RMSE/percentis, bias por relevo/cobertura e superfície de incerteza | erro vertical e espacial compatível com a menor queda/margem de projeto; decisão permanece estável no envelope de incerteza; regiões inconclusivas ficam bloqueadas |
| Colheitabilidade geométrica estática | `IMPLEMENTADO` | CF0 mede raio conservador e greide longitudinal das linhas | não representa cabeceira, última carreta, rampa transversal, rugosidade vertical nem controle dinâmico | raio de trabalho do implemento e limites de greide declarados | toda linha publicada como geometricamente aceita atende aos thresholds rastreados; falhas permanecem em `diagnostic_rows` e nunca são guia |
| Colheitabilidade da frota completa | `ESPECIFICADO` | regras de raio de manobra, cabeceira, offtracking, rampas, perfil e portais | não existe simulador cinemático 3D integrado nem biblioteca homologada de máquinas | geometrias/articulações, balanços, pneus, velocidades, limites OEM, envelopes vazio/carregado, manobras e terreno 3D | P95/máximo de cada eixo dentro do corredor; manobras cabem; limites longitudinal, transversal, vertical e OEM passam em todos os estados |
| Tráfego controlado | `ESPECIFICADO` | `TRAFFIC_001`, evidência agronômica e métricas de área única/repetição | bitolas e envelopes reais não foram reconciliados; não há plano persistente de faixas | espaçamento, bitolas de todos os eixos, larguras de pneus, erro RTK, tolerâncias, sobreposição e mapa de soqueiras | rodas permanecem nas faixas aprovadas em trabalho, manobra e transporte; invasão da linha/soqueira igual a zero ou exceção agronômica assinada |
| Padrão geométrico de linhas | `IMPLEMENTADO` | cenários E0 de triagem e CF0 geram famílias com identidade e métricas | E0/CF0 não codificam comandos de implemento nem continuidade operacional certificada | fase, orientação, espaçamento, recortes físicos, barreiras e identificadores globais | geometria reproduzível, sem cruzamentos proibidos, com cobertura/spacing auditáveis e sem alegação de guidance |
| Padrão operacional `guidance/work/movement` | `ESPECIFICADO` | [motor de cenários](MOTOR_DE_CENARIOS_E_SULCACAO.md) separa `guidance_line`, `worked_segment` e `movement_segment` | modelo não é emitido por um gerador operacional integrado | superfícies `WORK/LIFT/CROSS/TURN`, portais, permissões, sequência, comandos e causas de quebra | cada metro da guia tem estado inequívoco; trabalho não atravessa barreiras; cruzamentos e manobras possuem permissão e envelope aprovados |
| Envelope do controlador e exportação | `BLOQUEADO` | requisitos de simulação e pacote por máquina estão documentados | faltam formato real, modelo/display, firmware, limites de pontos/curvas e ensaio na máquina; não há exportador homologado | OEM, modelo, firmware, CRS/unidade, formato, densificação, precisão, comandos de implemento e tolerâncias de importação/seguimento | round-trip sem alteração material; teste em simulador e veículo; erro P95/máximo dentro do limite; comportamento fail-closed quando o formato não representa os estados |
| Estados vazio/carregado, custo roteável | `IMPLEMENTADO` | estágio estático de POA aceita custos/velocidades por estado e produz atribuição roteável | implementação é isolada e estática; não simula dinâmica articulada nem fila da frente | rede, restrições, velocidades/custos por estado, capacidade e produtividade | manifesto do estágio válido; rotas alcançáveis e capacitadas; métricas vazio/carregado separadas; nenhuma alegação de simulação integrada |
| Estados vazio/carregado, dinâmica e segurança | `ESPECIFICADO` | [POA e logística](POA_E_LOGISTICA_DE_COLHEITA.md) define ciclo, filas e rotas distintas | não há simulador de eventos discretos integrado nem envelope por carga/umidade | massa/carga por eixo, centro de gravidade, frenagem, rampa, velocidade, serviço, falha e disponibilidade por estado | nenhuma rota viola OEM, suporte ou envelope; filas/esperas P95 e sincronização aprovadas; resultados robustos a produtividade, chuva e falha |
| Umidade e compactação | `ESPECIFICADO` | `WET_SOIL_001`, estados sazonais e capacidade/precompressão estão definidos | faltam curvas locais, cargas/pneus completos e modelo de transmissão de tensão validado | teor de água/cenários, textura/perfil, tensão de precompressão/capacidade, pressão dos pneus, eixos, repetições e histórico | tensão aplicada com margem aprovada em cada célula/estado; rotas fecham ou limitam carga quando necessário; política substituta conserva incerteza e autoria |
| POA estático capacitado | `IMPLEMENTADO` | `optimize_static_poa.py` possui contrato, roteamento, atribuição capacitada e self-test | não traduz automaticamente o pedido central nem valida piso, drenagem e giro em campo | candidatos poligonais, rede, restrições, produtividade, capacidades e custos | estágio reproduzível e válido; balanço de massa, capacidade e alcançabilidade fecham; saída declara explicitamente o escopo estático |
| POA e logística integrada | `ESPECIFICADO` | modelo de localização, frota, baias, filas, swap e Pareto documentado | não há orquestração sulcação-sequência-transbordo-caminhão-usina nem dados operacionais calibrados | POAs físicos, vias, frota, turnos, produtividade, eventos de carga, filas, balança/usina, falhas e custos | nenhuma massa sem destino; throughput e filas P95 dentro do SLA; espera da colhedora e km/t publicados; POA passa água, solo, giro, energia e licença |
| Drenagem subsuperficial | `BLOQUEADO` | sinais de surgência, lençol raso e alagamento estão definidos como blocker | LiDAR/ortomosaico não caracterizam fluxo subsuperficial nem autorizam dreno | piezometria sazonal, perfis de solo, condutividade por horizonte, nível freático, surgências, qualidade da água, descarga e especialista responsável | diagnóstico hidrogeológico e projeto específico assinados; interferência com estruturas superficiais verificada; sem esses dados, somente mapa de suspeita e bloqueio |
| Ciclo multi-cortes e O&M | `ESPECIFICADO` | preservação de soqueiras, manutenção e estados degradados aparecem nos contratos | não há horizonte de ciclo, modelo de degradação, histórico de inspeção nem custo de vida útil | número de cortes, reforma, persistência das linhas, recalque/assoreamento, erosão, tráfego por safra, manutenção, downtime e custos descontados | gates passam em cana-planta e soqueiras; capacidade mínima permanece após degradação; plano de inspeção/reparo e custo de ciclo são aprovados |
| Qualidade `cut-to-mill` | `ESPECIFICADO` | esta versão introduz o tempo corte-entrega-moagem como saída logística e gate/SLA local | não há integração com timestamps de colheita, balança, fila, moagem e laboratório, nem curva local de deterioração | timestamps, lote/variedade/maturação, temperatura/chuva, impurezas, POL/Brix/pureza/ATR/dextrana quando disponíveis, turnos e SLA da usina | balanço temporal e de massa fecha; P95 e máximo corte-moagem atendem ao limite aprovado; modelo de qualidade é validado em dados locais e nunca usa perda universal importada |

### 3.3 Limite de alegação

Hoje existem dois produtos executáveis relevantes para esta matriz: a geometria CF0, sempre não hidráulica, e o estágio estático de POA, sempre isolado. Os cenários C1, C2 e C3 são **especificações de produto**, não solvers executáveis. Os validadores de configuração apenas impedem contratos incoerentes; eles não calculam terraços, ESD, PCE ou PCX.

## 4. Matriz de conservação e hidráulica

| ID | Regra verificável | Insumo mínimo | Métrica/prova | Falha | Natureza |
|---|---|---|---|---|---|
| `BASIN_001` | bacia não termina no talhão | MDT cobrindo montante e jusante, drenagem e obras | área contribuinte completa e exutório conhecido | bloquear E2/E3 | gate nacional de produto |
| `PCE_001` | risco de erosão é calculado separadamente | solo, relevo, cobertura e manejo por fase | perda/suscetibilidade, concentração e práticas | bloquear recomendação conservacionista | IAC + inferência de produto |
| `PCX_001` | volume, pico, condução, armazenamento e falha são calculados | chuva/IDF, infiltração, bacia, seções e receptores | balanço de massa, Q, nível, velocidade, tensão e margem | bloquear E2/E3 | IAC/ANA + inferência de produto |
| `RUSLE_001` | RUSLE/RUSLE2 não substitui PCX | fatores rastreáveis quando usada | somente perda média laminar/em sulcos | bloquear alegação de capacidade hidráulica | limitação do método USDA |
| `RECEIVER_001` | todo alcance possui receptor estável e verificado | geometria, seção, cobertura, jusante e permissões | capacidade e caminho nominal/de falha | bloquear alcance | gate nacional de produto |
| `APP_001` | APP não é receptor automático | APP, drenagem, autorização e alternativas | análise hidráulica, ambiental e legal | bloquear descarga/obra | legislação + inferência de segurança |
| `CEV_001` | CEV natural e construído são classes explícitas | origem, levantamento, cobertura e as built | `origin`, seção, estabilidade, manutenção | bloquear receptor não comprovado | IAC + inferência de modelagem |
| `CEV_SP_002` | no pack IAC/SP, CEV/prado é estabilizado antes das conexões | cronograma e inspeção de cobertura | estado `STABILIZED_AND_ACCEPTED` | bloquear conexão de TD/ST | regra oficial regional |
| `TI_001` | TI armazena a chuva adotada com bordo livre | solo/perfil, área, chuva e seção | volume útil >= volume de projeto | reprovar TI | IAC regional + hidráulica |
| `TI_002` | TI esvazia sob chuvas consecutivas | infiltração por estado, umidade antecedente e sequência | nível residual antes do evento seguinte | reprovar TI ou gerar TD/híbrido | evidência primária + segurança |
| `TD_001` | TD conduz Q de projeto sem erosão/assoreamento crítico | IDF, Tc, área, seção, cobertura | capacidade, velocidade/tensão e bordo livre | reprovar TD | IAC regional + hidráulica |
| `TD_SP_002` | TD paulista descarrega em CEV/prado previamente estabilizado | receptor e sequência executiva | conexão aceita após estabilização | bloquear implantação/conexão | rule-pack IAC/SP |
| `ST_SP_001` | ST paulista atende elegibilidade e mantém PCX | solo, rampa, cobertura, risco e drenagem | elegibilidade documentada | inovação/revisão especial ou reprovação | rule-pack IAC/SP |
| `ST_SP_002` | ST paulista inclui CEV/prado previamente estabilizado | receptor e sequência executiva | receptor aceito antes da liberação | bloquear ST | rule-pack IAC/SP |
| `ESD_001` | ESD é sistema difuso, não uma linha colorida | PCE/PCX, zonas, família completa e receptores | contribuição/capacidade/destino de cada alcance | bloquear rótulo ESD | ANA + evidência primária |
| `ESD_002` | não há concentração não dimensionada entre sulcos | MDT proposto e família inteira | convergência, transferências e transbordamento | reprovar família | hidráulica + inferência computacional |
| `ROAD_001` | estrada/carreador possui drenagem própria | superfície, sarjetas, bueiros e saídas | vazões e capacidade por trecho | bloquear uso como outlet incidental | IAC/ANA |
| `FAIL_001` | consequência de exceder capacidade é conhecida | superfícies e ativos a jusante | mapa de falha nominal/adverso | bloquear E2/E3 | inferência de segurança |
| `SUBSURFACE_001` | surgência, lençol raso ou alagamento persistente não são resolvidos por estruturas superficiais | piezometria sazonal, perfil, condutividade e investigação especializada | diagnóstico hidrogeológico e interação superfície-subsuperfície | bloquear recomendação apenas superficial | inferência de produto conservadora |
| `MDT_UQ_001` | queda e greide só são conclusivos quando excedem a incerteza vertical relevante | datum, checkpoints independentes e resíduos espaciais | margem da decisão no envelope de erro | bloquear E2/E3 ou marcar região inconclusiva | contrato de qualidade geoespacial |
| `HYD_OM_001` | capacidade deve permanecer válida no estado degradado e manutenível | vida útil, assoreamento, recalque, cobertura, obstrução e acesso | capacidade mínima, frequência de inspeção/reparo e caminho de falha | reprovar solução sem O&M | ANA/IAC + inferência de ciclo de vida |

### 4.1 Regra nacional e exceção regional paulista

O [Manual ANA, volume 5](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5) admite CEV de forma condicional (`se houver`). Por isso, o gate nacional do produto é **receptor estável e verificado**, que pode ser natural protegido ou estrutura dimensionada.

O [Boletim Técnico IAC 216](https://www.iac.sp.gov.br/publicacoes/publicacoes/iacbt126.pdf) é mais específico para cana e para o contexto paulista: TD e ST devem trabalhar com CEV/prado escoadouro implantado e estabilizado previamente. O motor aplica a regra mais restritiva quando o pack `BR-SP-CANA-IAC216` estiver ativo. Comprimentos, greides, velocidades, declividades e períodos de retorno citados pelo boletim ficam restritos a esse pack e ainda exigem recálculo local.

### 4.2 CEV natural ou construído

| Classe | Condição de entrada | O que não basta |
|---|---|---|
| `NATURAL` | prado/depressão existente, cobertura, seção e estabilidade verificadas para toda a vazão | ser talvegue, APP ou curso d'água |
| `CONSTRUCTED` | canal executado conforme projeto, protegido, vegetado e aceito após estabilização | existir apenas no desenho ou ter sido recém-movimentado |

As duas classes exigem descarga final, manutenção, acesso de inspeção e teste de falha. APP permanece uma restrição ambiental, não uma classe de receptor.

### 4.3 TI e eventos consecutivos

O TI não pode ser validado com um único evento sobre solo inicialmente seco. O envelope deve incluir umidade antecedente e sequência de chuvas, mantendo o nível residual do primeiro evento como condição inicial do seguinte. A tese de [Franco, ESALQ/USP](https://www.teses.usp.br/teses/disponiveis/11/11140/tde-21012019-150102/publico/Alexandre_Puglisi_Barbosa_Franco_versao_revisada.pdf) documenta variação espacial/temporal da infiltração e falhas após sequências de chuva; isso fundamenta o gate, mas não fornece um coeficiente universal.

### 4.4 RUSLE e PCX

A [documentação USDA-NRCS](https://www.nrcs.usda.gov/sites/default/files/2022-10/National-Agronomy-Manual.pdf) restringe RUSLE à erosão laminar e em sulcos e separa erosão em canais concentrados. Portanto:

```text
RUSLE -> indicador PCE de perda média/suscetibilidade
PCX   -> chuva-excesso, vazão, armazenamento, condução, estruturas e falha
```

Usar RUSLE como substituto de hidrologia/hidráulica deve gerar `BLOCKED_MISSING_PCX`.

### 4.5 Drenagem subsuperficial

MDT, nuvem de pontos e ortomosaico podem apontar depressões, umidade aparente, surgências e persistência de alagamento, mas não identificam sozinhos nível freático, fluxo por horizonte ou solução de drenagem. Esses sinais geram uma zona `SUBSURFACE_INVESTIGATION_REQUIRED`, retiram a área da geração automática e abrem levantamento com piezometria sazonal, perfis de solo, condutividade, destino da descarga e responsável habilitado. Não se desenha dreno enterrado por inferência visual.

## 5. Matriz de colheitabilidade

| ID | Regra verificável | Insumo mínimo | Métrica/prova | Falha |
|---|---|---|---|---|
| `WORK_RADIUS_001` | raio estático da linha trabalhada >= requisito do implemento | geometria e `minimum_work_path_radius_m` | raio mínimo/P05 e mudança de curvatura | reprovar linha |
| `TURN_RADIUS_001` | manobra é cinematicamente possível | veículo, articulações e `minimum_turn_radius_m` | envelope por manobra `U/Omega/T/P` | reprovar cabeceira/manobra |
| `HEADLAND_001` | largura deriva da manobra e frota, não de constante | envelopes vazio/carregado e folgas | largura necessária x disponível | reprovar endpoint |
| `OFFTRACK_001` | última unidade permanece no corredor permitido | frota completa e terreno 3D | máximo/P95 por eixo e invasão da soqueira | reprovar linha/portal/rota |
| `SLOPE_001` | rampas longitudinal e transversal atendem frota/OEM | MDT e limites por máquina/carga | `grad(z) dot t` e `grad(z) dot n` | reprovar trecho |
| `VERTICAL_001` | quebra vertical e altura de corte permanecem válidas | perfil 3D, entre-eixos e mecanismo de corte | roughness, pitch e margem do cortador | reprovar trecho |
| `TRAFFIC_001` | todas as bitolas são compatíveis com as faixas de tráfego | eixos, pneus, espaçamento e RTK | área única trafegada e sobreposição em cultura | reprovar plano de tráfego |
| `WET_SOIL_001` | tensão aplicada <= capacidade no teor de água simulado | curva de precompressão/capacidade, pneus e cargas | margem por eixo/célula e cenário | fechar rota/POA/manobra |
| `PORTAL_001` | conexão possui permissão, tangência, raio, perfil, drenagem e envelope | limites, corredor, frota e uso | todos os checks por portal | manter talhões separados |
| `POWER_001` | eixo elétrico 2D é barreira opaca sem clearance 3D | shape e buffer aprovado | nenhuma linha/envelope cruza faixa | quebrar linha e rota |
| `LINE_STATE_001` | guia, trabalho e deslocamento são objetos distintos | superfícies operacionais, barreiras, portais e sequência | cobertura `WORK/LIFT/CROSS/TURN` sem ambiguidade | bloquear exportação operacional |
| `CTRL_001` | controlador só recebe geometria e comandos que o modelo/firmware comprovadamente representa | OEM, display, firmware, formato e ensaio | round-trip, densificação, erro de seguimento e estados preservados | bloquear exportação para máquina |
| `LOAD_STATE_001` | vazio e carregado possuem envelopes, cargas e limites próprios | massa, eixos, centro de gravidade, pneus, velocidade e OEM | segurança, offtracking, suporte e custo por estado | reprovar rota ou limitar carga |
| `RATOON_001` | tráfego e corte preservam a linha/soqueira ao longo dos cortes planejados | mapa de linhas persistentes, danos, tráfego por safra e reforma | invasão, dano basal, falhas e custo acumulado | reprovar plano de ciclo |

### 5.1 Raio de trabalho não é raio de manobra

`minimum_work_path_radius_m` verifica a curvatura enquanto sulcador, plantadora ou colhedora executa trabalho. `minimum_turn_radius_m` alimenta o modelo cinemático da cabeceira. Um valor não pode preencher o outro; a falta de qualquer um mantém o respectivo resultado como `NOT_EVALUATED`.

### 5.2 Conjunto articulado e offtracking

O eixo do trator não representa o conjunto. O ensaio de [Passalaqua e Molin (2020)](https://doi.org/10.1590/1809-4430-Eng.Agric.v40n2p223-231/2020) mediu erros crescentes nos reboques de transbordo em curvas e declive lateral, mesmo com direção automática. Os números observados são evidência de risco, não tolerâncias universais. A aprovação exige simulação da colhedora/plantadora e de cada composição real, vazia e carregada.

### 5.3 Solo úmido e tráfego controlado

Tráfego controlado é a compatibilidade geométrica de todos os eixos com as entrelinhas permanentes, somada ao controle de trajetória. Em experimento com cana, [Souza et al. (2015)](https://doi.org/10.1590/0103-9016-2014-0078) encontraram melhor qualidade física e maior massa radicular sob tráfego controlado. Isso não autoriza tráfego em qualquer umidade.

O cenário deve cruzar pressão/tensão transmitida por eixo com capacidade de suporte ou tensão de precompressão por teor de água. Sem curva local, aplica-se a política conservadora aprovada e registra-se a incerteza; não se presume trafegabilidade. A rota operacional possui estados sazonais `OPEN`, `LOAD_LIMITED` e `CLOSED_WET_SOIL`.

### 5.4 Padrão de linhas e envelope do controlador

A família geométrica não é o arquivo da máquina. O padrão operacional conserva a identidade da guia e separa os trechos `WORK`, `LIFT`, `CROSS` e `TURN`; uma quebra por terraço, carreador, rede elétrica, limite sem permissão ou obstáculo não pode ser apagada para criar um tiro artificialmente longo. Conexões entre talhões só existem por portal aprovado, ou por dissolução de uma divisa meramente cadastral dentro do mesmo escopo autorizado e sem feição física.

O envelope deve ser testado por combinação `máquina + implemento/composição + display + firmware + formato`. Simplificação e densificação respeitam erro transversal, curvatura e limite de pontos. A importação de volta e o ensaio de seguimento devem preservar CRS, unidade, ordem, sentido e comandos; incapacidade do formato em representar estados gera arquivos separados ou bloqueio, nunca uma polilinha ambígua.

## 6. Operação e logística depois dos gates

| Objetivo | Métricas obrigatórias | Interpretação |
|---|---|---|
| tiros úteis | comprimento físico P10/P50/P90, % linhas curtas, manobras/ha | maior pode ser melhor, sem meta universal de comprimento |
| continuidade | `WORK/LIFT/CROSS/TURN`, portais e tempo improdutivo | cruzar carreador não prolonga automaticamente o alcance hidráulico |
| capacidade CTT | t/h, velocidade por estado, perdas e espera | simular faixa, não velocidade fixa |
| transbordos | ciclo, capacidade, quantidade, espera e sincronização | tiro longo pode exigir mais frota e elevar compactação |
| tráfego | km/t, área única, repetições, invasão da soqueira | minimizar dano, sobretudo carregado e úmido |
| POA | distância roteável, baias, fila, giro, piso, drenagem e throughput | POA é polígono capacitado, não ponto euclidiano |
| estados de carga | km/t, tempo, rampa, velocidade, suporte e envelope separados para vazio/carregado | o caminho ótimo e a condição de segurança podem mudar com a carga |
| ciclo multi-cortes | valor/custo por corte, dano à soqueira, persistência das linhas, degradação e reforma | benefício de uma safra não compensa perda de longevidade sem explicitação |
| O&M | inspeções, assoreamento, recalque, obstrução, reparos, indisponibilidade e custo de vida útil | comparar capacidade no estado novo e no estado degradado manutenível |
| qualidade `cut-to-mill` | P50/P95/máximo corte-entrega-moagem, massa em fila e indicadores locais de qualidade | tiro longo só agrega valor se a matéria-prima chegar e for processada dentro do SLA |
| robustez | P10/P50/P90 de produtividade, chuva, velocidade, falha e via degradada | selecionar alternativa estável, não só a melhor média |

Resultados publicados mostram potencial de reduzir manobras por `direct/long shot`, mas não autorizam conexões sem gates. O caso de [Santoro, Soler e Cherri (2017)](https://doi.org/10.1016/j.compag.2017.07.013) deve calibrar comparações contra o baseline isolado, não prometer ganho fixo.

### 6.1 Dimensionamento mínimo do ciclo

Para cada frente e cenário:

```text
N_transbordos >= ceil(Q_colhedora * T_ciclo / capacidade_util_transbordo)
```

`T_ciclo` inclui acompanhar a colhedora, saída, viagem carregada, fila, descarga no POA, retorno vazio e margem de sincronização. A infraestrutura de carregamento reduz a entrada de caminhões no canavial e o risco de compactação, como descreve a [Embrapa](https://www.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/cana-de-acucar/producao/planejamento-da-colheita/colheita/carregamento).

### 6.2 POA

O POA possui `polygon`, acessos, baias, filas, envelopes, piso/capacidade de suporte, drenagem, restrições ambientais/elétricas e capacidade horária. Sua localização é problema capacitado na rede dirigível. Só entram no Pareto locais aprovados nos gates de água, solo, giro, licenciamento e segurança.

### 6.3 Vazio e carregado

O grafo pode ter sentido, velocidade, restrição de rampa, limite de carga e disponibilidade diferentes por estado. A composição carregada usa sua massa por eixo, centro de gravidade, frenagem, offtracking e capacidade de suporte; o retorno vazio não herda automaticamente a mesma rota. Resultados agregados que não separam os dois estados não servem para dimensionar frota, compactação ou segurança.

### 6.4 Ciclo multi-cortes e O&M

A alternativa deve ser avaliada no horizonte aprovado de cana-planta, soqueiras e reforma. A cada corte, o estado inclui persistência das linhas, dano basal, falhas, tráfego acumulado, recalque e trilhas de roda, perda de seção, assoreamento, cobertura vegetal, obstrução e acesso de manutenção. C1, C2 e C3 precisam ser rechecados no estado degradado manutenível; C2 não permanece “passante” apenas porque a seção nova era trafegável.

O plano de O&M define inspeção antes da safra, após eventos de chuva relevantes e após a colheita, responsáveis, gatilhos de fechamento, reparos, prazo de resposta e evidência as built. O Pareto inclui custo e indisponibilidade de ciclo de vida. Ausência de histórico local mantém degradação e custo como intervalos de incerteza, não como zero.

### 6.5 Qualidade `cut-to-mill`

O relógio começa no corte do lote e termina na moagem, não na chegada ao POA. Devem ser encadeados colheita, troca de transbordo, descarga, carregamento, viagem rodoviária, balança, pátio e alimentação da usina. A [Embrapa](https://portaldxp-h.sede.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/cana/pos-producao/gestao-industrial/qualidade-de-materia-prima) trata o tempo de corte/moagem como fator de qualidade; estudos de deterioração, como [Misra et al. (2020)](https://doi.org/10.1016/j.sjbs.2019.09.028), mostram dependência de condição e matéria-prima. Portanto, nenhum percentual de perda ou limite de horas de outro local entra como default.

O produto publica P50, P95 e máximo do tempo, massa por faixa de idade e violações do SLA definido pela usina. Quando houver dados locais, o modelo de qualidade usa lote/variedade/maturação, condições meteorológicas, impurezas e análises de POL, Brix, pureza, ATR, açúcares redutores ou dextrana, com validação fora da amostra. Sem timestamps e laboratório coerentes, o motor pode otimizar tempo e fila, mas deve declarar `QUALITY_EFFECT_NOT_EVALUATED`.

## 7. Parâmetros que o usuário ou rule-pack deve fornecer

| Grupo | Parâmetros essenciais |
|---|---|
| solo | perfil, Ksat/infiltração por estado, erodibilidade, cobertura, compactação, capacidade/precompressão por umidade |
| chuva | IDF, duração/Tc, período de retorno, distribuição temporal, umidade antecedente, sequência para TI |
| hidráulica | seções, rugosidade por fase, velocidades/tensões admissíveis, bordo livre, obstrução e manutenção |
| frota | dimensões, bitolas, eixos, pneus, pressões, cargas vazia/cheia, articulações, velocidades e limites OEM |
| geometria | espaçamento, tolerância, raio de trabalho, raio de manobra, transição de curvatura e folgas |
| controlador | OEM, máquina, display, firmware, formato, CRS/unidade, limites de ponto/curva, comandos e erro admissível |
| operação | produtividade P10/P50/P90, capacidade do transbordo, tempos, estados e calendário de colheita |
| infraestrutura | carreadores, portais, pontes/bueiros, vias sazonais, rede elétrica, APP e permissões |
| POA | candidatos, piso, drenagem, baias, fila, giro, capacidade e acesso de caminhão |
| ciclo e O&M | cortes planejados, reforma, degradação de seção/via, inspeções, reparos, indisponibilidade e custo de vida útil |
| cut-to-mill | timestamps por lote, SLA da usina, turnos, pátio/moagem, clima, variedade, maturação, impurezas e qualidade laboratorial |
| incerteza MDT | datum/geóide, checkpoints independentes, resíduos, bias, superfície de incerteza e menor queda decisória |
| subsuperfície | piezometria sazonal, perfis, condutividade por horizonte, surgências, descarga e autoria especializada |
| decisão | perfil conservação/equilíbrio/operação, pesos apenas entre aprovados e limiares de robustez |

Todo parâmetro deve possuir unidade, fonte, data, método, incerteza e responsável. Valor ausente em gate produz `NOT_EVALUATED` ou `BLOCKED_MISSING_INPUT`, nunca aprovação por default.

## 8. Produtos por nível de evidência

| Nível | Pode entregar | Não pode afirmar |
|---|---|---|
| `E0` | MDT, curvas, fluxo topográfico e linhas diagnósticas | segurança hidráulica, colheitabilidade ou ESD aprovado |
| `E1` | famílias e manobras condicionadas à frota/interferências | conservação executiva sem solo/chuva/receptores |
| `E2` | PCE/PCX, sistemas C1-C3, logística e Pareto condicionados | locação executiva sem campo/as built/aprovação |
| `E3` | arquivos controlados, memorial, implantação e manutenção | eliminar responsabilidade técnica e monitoramento |

## 9. Critério de aceitação de um cenário

Um cenário só entra como alternativa selecionável quando:

1. possui insumos suficientes para seu nível;
2. passa todos os gates nacionais e o rule-pack aplicável;
3. fecha a água até receptor e jusante, inclusive no caso de falha;
4. prova dirigibilidade da frota completa e trafegabilidade por umidade;
5. preserva linha/soqueira sob envelope e tráfego controlado;
6. dimensiona transbordos, rotas e POA sem instabilidade de filas;
7. apresenta métricas com incerteza e comparação contra baseline;
8. demonstra compatibilidade no controlador/modelo/firmware quando houver exportação para máquina;
9. preserva desempenho em cana-planta, soqueiras e estado degradado manutenível;
10. fecha o tempo corte-moagem e a massa dentro do SLA local, ou declara o efeito de qualidade como não avaliado;
11. mantém revisão de campo, responsável, plano de O&M e versão rastreáveis.

Caso nenhum candidato passe, o produto correto é `NO_FEASIBLE_CANDIDATE`, acompanhado dos blockers e dos insumos necessários para nova rodada.

## 10. Referências primárias e oficiais que sustentam os gates

As fontes abaixo sustentam relações, riscos e métodos. Resultados numéricos de experimento, fazenda, variedade ou frota continuam proibidos como default universal.

| Tema | Referência | Uso nesta matriz |
|---|---|---|
| sistematização da cana | [Embrapa, sistematização da área para plantio mecanizado](https://ainfo.cnptia.embrapa.br/digital/bitstream/item/136221/1/2015AP19.pdf) | integrar curso da água, terraceamento, tiros, espaçamento/rodagem, solo e relevo; não autoriza uma geometria universal |
| conservação regional | [IAC, Boletim Técnico 216](https://www.iac.sp.gov.br/publicacoes/publicacoes/iacbt126.pdf) | PCE/PCX, TI/TD/ST e precedência de CEV/prado no pack paulista |
| conservação nacional | [ANA, Manual do Programa Produtor de Água, volume 5](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5) | práticas, dimensionamento e receptor estável sem transformar CEV em obrigação nacional incondicional |
| limite da RUSLE | [USDA-NRCS, National Agronomy Manual](https://www.nrcs.usda.gov/sites/default/files/2022-10/National-Agronomy-Manual.pdf) | separar erosão laminar/em sulcos de hidrologia, canais concentrados e PCX |
| infiltração e eventos consecutivos | [Franco, ESALQ/USP](https://www.teses.usp.br/teses/disponiveis/11/11140/tde-21012019-150102/publico/Alexandre_Puglisi_Barbosa_Franco_versao_revisada.pdf) | exigir estado antecedente, variabilidade e esvaziamento de TI sem importar coeficiente universal |
| acurácia e incerteza do terreno | [Incra, Manual Técnico de Georreferenciamento](https://www.gov.br/incra/pt-br/assuntos/governanca-fundiaria/Manual_Tecnico_de_Georreferenciamento_2_Edicao.pdf/%40%40display-file/file) e [ASPRS 2024](https://old.asprs.org/archives/asprs-approves-edition-2-version-2-of-the-asprs-positional-accuracy-standards-for-digital-geospatial-data-2024.html) | exigir datum, checkpoints independentes, resíduos e classe declarada; compatibilidade com a menor queda continua sendo gate do projeto |
| conjunto articulado | [Passalaqua e Molin (2020)](https://doi.org/10.1590/1809-4430-Eng.Agric.v40n2p223-231/2020) | justificar envelope de toda a composição e risco maior em curvas/declive lateral |
| tráfego controlado | [Souza et al. (2015)](https://doi.org/10.1590/0103-9016-2014-0078) e [Luz et al. (2023)](https://doi.org/10.1016/j.geoderma.2023.116427) | relacionar localização do tráfego, estrutura/funções do solo e raízes; limites continuam dependentes do solo e da frota |
| umidade, carga e compactação | [Esteban et al. (2020)](https://doi.org/10.1016/j.geoderma.2019.114097), [Guimarães Júnnyor et al. (2019)](https://doi.org/10.1590/1678-992x-2018-0052) e [Esteban et al. (2024)](https://doi.org/10.1016/j.still.2024.106206) | separar faixa de tráfego, máquina, carga, repetição e condição do solo; resultados experimentais não substituem a curva local de suporte |
| capacidade operacional | [estudo de capacidade da colheita mecanizada](https://www.scielo.br/j/eagri/a/8ZzkNDQ4D9kd9N5qQc6Gs5s/) | comprimento de linha e disponibilidade de transbordos/caminhões afetam capacidade; tiros longos não bastam |
| sincronização colhedora-transbordo | [Magalhães, Baldo e Cerri (2008)](https://doi.org/10.1590/S0100-69162008000200008) e [Corrêa et al. (2025)](https://doi.org/10.3390/agriengineering7020025) | modelar acompanhamento e manobra conjunta; soluções e ganhos observados continuam condicionados à frota e ao terreno |
| rotas entre talhões | [Santoro, Soler e Cherri (2017)](https://doi.org/10.1016/j.compag.2017.07.013) | comparar continuidade e manobras com baseline, sem prometer ganho fixo |
| coordenação corte-transporte-moagem | [Lamsal, Jones e Thomas (2016)](https://doi.org/10.1287/trsc.2015.0650) | tratar colheita, transporte e usina como programação coordenada, não otimizações isoladas |
| qualidade pós-corte | [Embrapa, qualidade da matéria-prima](https://portaldxp-h.sede.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/cana/pos-producao/gestao-industrial/qualidade-de-materia-prima) e [Misra et al. (2020)](https://doi.org/10.1016/j.sjbs.2019.09.028) | tornar corte-moagem observável e calibrado por condição, lote e usina |
| drenagem subsuperficial | [Shinzato et al. (2013)](https://doi.org/10.11357/jsam.75.426) | evidenciar que a resposta a subsolagem/dreno foi estudada em solo e condição locais; por inferência conservadora do produto, isso não vira solução derivada apenas do MDT |

O desenho final deve registrar, para cada parâmetro derivado dessas fontes, se ele é apenas hipótese de triagem, intervalo de sensibilidade, calibração local ou regra aprovada. A fonte nunca substitui a medição necessária para o gate.
