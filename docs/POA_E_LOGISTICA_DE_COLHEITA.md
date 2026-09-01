# POA e Logística Integrada da Colheita

## 1. Objetivo

Este documento especifica como o projeto de sistematização deve incorporar o **POA - Ponto de Operação Agrícola**, a logística dos transbordos, o carregamento dos caminhões e a entrega da cana à usina. O propósito não é apenas localizar um pátio próximo ao talhão. É comparar arranjos completos de sulcação, sequência de colheita, tráfego, POAs, frota e acessos sem comprometer conservação, segurança ou longevidade do canavial.

O POA será tratado como um **pátio operacional em polígono**, nunca como um ponto matemático. Um centróide pode servir como rótulo ou nó simplificado do grafo, mas não prova que o conjunto mecanizado cabe, gira, espera, descarrega ou sai com segurança.

O cenário passa a ter a seguinte composição:

```text
cenario_integrado = sistema_conservacionista
                   x familia_de_sulcacao
                   x conexao_entre_talhoes
                   x sequencia_de_colheita
                   x plano_de_POAs
                   x politica_de_despacho
                   x perfil_de_frota
                   x condicao_operacional
                   x envelope_de_incerteza
```

Restrições hidráulicas, ambientais, fundiárias, elétricas, mecânicas e de segurança são eliminatórias. Ganhos de tiro, distância, fila ou custo não podem compensar a violação de um hard gate.

## 2. Invariantes do domínio

O produto deve manter separadas as seguintes entidades:

```text
sulco_fisico
!= guia_operacional
!= rota_do_transbordo
!= alcance_hidraulico
!= rota_do_caminhao
```

Também devem permanecer distintos:

```text
ponto_estimado_de_enchimento
!= janela_segura_de_troca
!= portal_de_saida_do_talhao
!= baia_de_transferencia
!= POA
```

Um tiro longo pode reduzir manobras da colhedora e, simultaneamente, aumentar o percurso carregado do transbordo. Um POA central pode reduzir a distância interna e aumentar a distância ou o risco rodoviário. Um local plano pode estar sobre talvegue, rede elétrica, solo sem suporte ou drenagem inadequada. Por isso o motor deve apresentar alternativas não dominadas, e não uma única nota opaca.

## 3. Vocabulário canônico

| Entidade | Semântica |
|---|---|
| `operational_shot` | cadeia ordenada de trechos trabalhados e movimentos que a colhedora executa sem uma manobra terminal completa |
| `worked_segment` | trecho em que a colhedora realiza trabalho sobre a cultura |
| `load_event` | posição estimada onde uma unidade de transbordo atinge sua capacidade efetiva |
| `swap_window` | intervalo mecanizável no qual o transbordo cheio pode ser substituído pelo vazio |
| `field_exit_portal` | conexão aprovada entre linha/cabeceira e carreador |
| `transshipment_route` | percurso vazio ou carregado do transbordo no grafo operacional |
| `poa_site` | polígono reservado e preparado para transferência, circulação e espera |
| `transfer_bay` | posição e envelope em que o transbordo descarrega no veículo rodoviário |
| `queue_lane` | faixa de espera cuja ocupação não bloqueia acessos ou vias de circulação |
| `truck_route` | percurso do veículo rodoviário entre POA e destino industrial ou logístico |
| `transfer_event` | operação temporal de posicionar, transferir a carga e liberar a baia |
| `harvest_front` | conjunto de colhedoras, transbordos, veículos e recursos que operam coordenadamente |
| `dispatch_policy` | regra de alocação dos transbordos e caminhões entre frentes, eventos e POAs |

O termo POA é o nome de produto adotado neste projeto. Fontes públicas também usam `pátio de transferência`, `ponto de transferência` e `pátio de carregamento`. A persistência deve guardar `poa_site` como polígono e `transfer_bay` como componente interno, evitando que terminologias diferentes misturem local de carga no talhão com local de descarga no caminhão.

## 4. Decisão V1 para redes elétricas

### 4.1 Camada obrigatória ou declaração explícita

Na V1, a infraestrutura elétrica aérea será informada pela camada:

```text
power_line_axis
geometry: LineString 2D
```

Cada feição deve representar o eixo da rede desenhado pelo centro dos postes. Um arquivo com `MultiLineString` poderá ser ingerido, mas será normalizado em feições `LineString` antes do cálculo. Ortomosaico, classificação automática do LAZ ou visão computacional podem emitir alertas de conferência; não substituem a camada declarada.

Quando não existir rede aérea no escopo, o cliente deve fornecer uma declaração versionada contendo, no mínimo:

- fazendas, unidades legais e talhões cobertos;
- responsável pela declaração;
- data e fonte da verificação;
- texto explícito de que não há rede elétrica aérea conhecida no escopo;
- validade ou revisão à qual a declaração se aplica.

A ausência simultânea de `power_line_axis` e da declaração deixa o tema `UNRESOLVED`. Nesse estado, resultados topográficos preliminares podem ser visualizados, mas nenhuma solução operacional ou executiva é aprovada.

### 4.2 Buffer como barreira absoluta

O motor construirá:

```text
power_barrier = dissolve(
  buffer(power_line_axis, constraints.overhead_power_line_exclusion_half_width_m)
)
```

`constraints.overhead_power_line_exclusion_half_width_m` é um parâmetro obrigatório, versionado e acompanhado de fonte. Pode ser definido por regra aprovada, responsável de segurança, concessionária ou projeto específico. A plataforma não terá distância universal embutida e não transformará esse limite em preferência de otimização.

Na V1, `power_barrier` é uma barreira absoluta:

- a área operável é recortada pelo buffer;
- sulcos e segmentos trabalhados são quebrados na borda do buffer;
- linhas-guia não recebem conexão automática através do buffer;
- não se cria `crossing_node`, `portal` ou estado `LIFT_CROSS` sobre a rede;
- rotas vazias e carregadas de transbordos ficam fora do buffer;
- rotas e manobras de caminhões ficam fora do buffer;
- `swap_window`, `load_event` operacionalizado, fila, baia e POA ficam fora do buffer;
- nenhuma transferência ou descarga é permitida dentro do buffer.

Todo trecho interrompido recebe `termination_reason = POWER_LINE_BARRIER`. Componentes operáveis situados em lados opostos da barreira são tratados como zonas desconectadas, ainda que possuam a mesma direção ou fase de sulcação.

Esta regra é deliberadamente conservadora. Uma futura versão poderá estudar travessias com levantamento tridimensional, tensão, flecha, envelope vertical e aprovação formal, mas isso não faz parte da V1.

## 5. Contrato de dados

### 5.1 Terreno, limites e conservação

- talhões, fazendas, unidades legais e áreas cultiváveis;
- LAZ/LAS/COPC ou MDT com CRS, unidade e referência vertical conhecidos;
- checkpoints e incerteza compatíveis com o nível de entrega;
- estradas, carreadores, cabeceiras, plataformas e acessos;
- terraços, canais, valetas, CEV, bueiros, cursos d'água, APP e áreas úmidas;
- obstáculos, edificações, árvores isoladas relevantes, dutos e áreas de exclusão;
- `power_line_axis` ou declaração formal de ausência.

### 5.2 Produtividade e cultura

A preferência é por uma superfície espacial de produtividade em `t/ha`, acompanhada de ano, método, resolução, cobertura e erro. Quando houver apenas média por talhão, o motor pode gerar uma análise de sensibilidade, mas não deve apresentar a posição exata dos enchimentos como fato observado.

Também são necessários, quando aplicáveis:

- variedade, idade e estágio de corte;
- número de linhas colhidas simultaneamente;
- espaçamento real;
- perdas e impurezas esperadas;
- densidade aparente da carga ou fator volume/massa;
- condição de cana ereta ou acamada;
- incerteza da produtividade e da capacidade útil.

### 5.3 Perfil da colhedora e dos transbordos

Cada perfil de frota é versionado e contém:

- quantidade disponível e reserva;
- dimensões, massa vazia e carregada;
- capacidade nominal de massa e volume;
- quantidade de unidades rebocadas e articulações;
- bitola, pneus, pressão, eixos e cargas;
- altura normal e maior envelope operacional;
- raio de giro, ângulos de articulação e envelope varrido;
- limites de rampa, inclinação lateral, quebra vertical e suporte do piso;
- velocidade por superfície, inclinação, sentido e estado vazio/carregado;
- lado e modo de descarga;
- tempos de troca, posicionamento, basculamento, saída e retorno;
- disponibilidade, abastecimento, manutenção e distribuição de falhas;
- custos horários, por distância e por combustível.

O envelope precisa representar o conjunto completo. A trajetória do trator não prova que a segunda carreta permaneça na faixa permitida, principalmente em curvas e inclinação lateral.

### 5.4 Frota rodoviária e destino

- configurações de caminhão e implementos rodoviários;
- capacidade útil de massa e volume;
- dimensões, eixos, raio e envelope de manobra;
- velocidades por classe e condição de via;
- restrições de pontes, porteiras, vias públicas e horários;
- tempos de engate, pesagem, descarga, retorno e manutenção;
- quantidade, disponibilidade, custos e política de despacho;
- destino, capacidade de recebimento e meta horária da usina;
- janelas de entrega e requisitos de tempo corte-processamento.

### 5.5 Grafo viário

Cada aresta do grafo deve informar, sempre que aplicável:

- direção permitida e possibilidade de cruzamento;
- largura e comprimento;
- superfície e condição seca/úmida;
- declividade longitudinal e transversal;
- velocidade vazio e carregado;
- limite de massa/eixo e capacidade de suporte;
- portais, porteiras, pontes, bueiros e restrições;
- drenagem, alagamento e condição sazonal;
- autorização fundiária e rodoviária;
- interferências e barreiras, incluindo `power_barrier`.

Distância euclidiana serve apenas para triagem. Toda métrica operacional deve usar caminho dirigível no grafo, com custos diferentes para veículos vazios e carregados.

### 5.6 POAs existentes e áreas candidatas

O usuário pode enviar:

- polígonos de POAs existentes;
- polígonos onde novos POAs são permitidos;
- áreas proibidas para obra ou perda de cultivo;
- acessos, sentidos e baias existentes;
- superfície, estado de conservação, drenagem e capacidade de suporte;
- permissões, vigência e restrições de uso.

Quando o motor gerar candidatos, eles permanecem `UNCONFIRMED` até vistoria e validação do terreno. Nenhuma dimensão de pátio ou área atendida será universal: a geometria resulta da frota, do layout, da fila de projeto, do terreno e da demanda simulada.

## 6. Modelo de massa por tiro

Para uma linha parametrizada pelo estaqueamento `s`, a massa acumulada esperada é:

```text
M(s) = integral[0,s] Y(u) * W_efetiva(u) / 10.000 du
```

onde:

- `Y(u)` é a produtividade local em `t/ha`;
- `W_efetiva(u)` é a largura efetivamente colhida em metros;
- `M(s)` é a massa acumulada em toneladas.

A capacidade efetiva de uma unidade ou conjunto de transbordo é:

```text
C_efetiva = min(
  capacidade_massica,
  volume_util * densidade_aparente,
  limite_do_conjunto,
  limite_de_eixo_da_rota,
  limite_de_suporte_do_solo
)
```

Um `load_event_k` ocorre no primeiro estaqueamento em que:

```text
M(s_k) - M(s_{k-1}) >= C_efetiva
```

Para produtividade homogênea, a distância aproximada de enchimento é:

```text
L_enchimento = 10.000 * C_efetiva / (Y * W_efetiva)
```

Essa forma fechada serve para auditoria. O cálculo de produção usa a integral espacial, respeitando falhas, bordaduras, exclusões e variação de produtividade.

Cada evento deve carregar:

- posição e estaqueamento `P10/P50/P90`;
- massa e volume esperados;
- unidade que está sendo abastecida;
- tiro, segmento, talhão e frente de origem;
- direção e horário estimado;
- janela de troca associada;
- rotas viáveis aos POAs;
- fontes e incertezas.

## 7. Ciclo de transbordo e capacidade do POA

O ciclo completo será decomposto, nunca representado apenas por uma velocidade média:

```text
T_ciclo = T_entrada_e_acoplamento
        + T_enchimento
        + T_saida_do_talhao
        + T_viagem_carregado
        + T_fila_POA
        + T_posicionamento
        + T_transferencia
        + T_saida_POA
        + T_viagem_vazio
        + T_reaquisicao_da_colhedora
        + T_paradas
```

A produção instantânea esperada de uma frente pode ser estimada por:

```text
Q_frente(t) = soma Q_colhedora_h(t)
```

e a chegada média de cargas ao POA por:

```text
lambda_cargas(t) = Q_frente_atribuida(t) / C_efetiva_media(t)
```

Para uma baia, a taxa de serviço depende de posicionamento, transferência e liberação:

```text
mu_baia = 1 / E[T_posicionamento + T_transferencia + T_saida]
```

Com `b` baias equivalentes, `lambda < b * mu` é condição necessária de estabilidade, mas não garante fila aceitável. Variabilidade, bloqueios, sincronização dos conjuntos e espaço físico da fila exigem simulação de eventos discretos.

Uma aproximação inicial da quantidade de transbordos por colhedora é:

```text
N_base = ceil(T_ciclo / T_enchimento)
```

O número liberado para planejamento deve acrescentar disponibilidade, variabilidade, política de reserva e nível de serviço, todos parametrizados. A fórmula não substitui a simulação.

## 8. Grafos acoplados

O cenário mantém quatro representações simultâneas:

- `G_trabalho`: sulcos, tiros, estados da colhedora, cabeceiras e manobras;
- `G_logistico`: eventos de carga, trocas, rotas de transbordo, POAs, caminhões e usina;
- `G_hidraulico`: sulcos ativos, terraços, drenagem, transferências e receptores;
- `G_restricoes`: limites, exclusões, autorizações e barreiras como a rede elétrica.

O caminho logístico típico é:

```text
load_event
 -> swap_window
 -> faixa_de_trafego_controlado
 -> headland
 -> field_exit_portal
 -> carrier_edge
 -> poa_access
 -> queue_lane
 -> transfer_bay
 -> road_edge
 -> mill_or_destination
```

Cada nó e aresta possui geometria, estado temporal, veículo compatível, custo, capacidade e motivos de bloqueio. O caminho carregado e o retorno vazio podem ser diferentes.

## 9. Algoritmo de geração

### 9.1 Preparação e QA

1. Validar CRS, unidades, cobertura, revisão e precisão dos dados.
2. Normalizar talhões, rede viária, POAs, obstáculos e autorizações.
3. Construir `power_barrier` ou validar a declaração de ausência.
4. Recortar todas as áreas e grafos operacionais pelas barreiras absolutas.
5. Gerar superfície de inclinação, drenagem, suporte e incerteza.
6. Marcar dados como `MEASURED`, `MANUFACTURER`, `TELEMETRY`, `USER_DECLARED`, `RULE_PACK`, `INFERRED` ou `ASSUMED`.

### 9.2 Sulcação e sequência preliminar

1. Receber as famílias elegíveis de curva embutida, base larga/passante, ESD ou misto.
2. Quebrar sulcos e guias em `power_barrier` e demais barreiras.
3. Gerar sequências de colheita compatíveis com direção, cabeceira e frota.
4. Calcular massa por trecho e por tiro.
5. Projetar `load_events` sob cenários de produtividade e capacidade.

### 9.3 Janelas de troca

Ao redor de cada `load_event`, o motor buscará um intervalo em que seja possível trocar unidades sem parar ou danificar a cultura além do limite aceito. A janela será rejeitada quando:

- o envelope invadir linhas ou soqueiras não destinadas ao tráfego;
- houver curva, rampa, inclinação lateral ou quebra vertical incompatível;
- coincidir com terraço, canal, valeta, bueiro, APP ou obstáculo;
- interceptar `power_barrier`;
- bloquear uma rota ou criar conflito não resolvido entre veículos;
- não existir caminho seguro de saída e retorno.

Se a incerteza `P10-P90` do enchimento não couber em uma janela aceitável, o candidato recebe penalidade ou é bloqueado conforme a política operacional. O motor pode antecipar uma troca ou reorganizar a sequência, mas não pode empurrar o evento para uma área proibida.

### 9.4 Geração do POA

Os candidatos vêm de duas fontes:

1. polígonos existentes ou permitidos enviados pelo usuário;
2. áreas geradas ao longo de vias e interseções elegíveis.

Para cada candidato, o motor:

1. elimina interseções com `power_barrier` e outras exclusões;
2. verifica acesso rodoviário e agrícola;
3. projeta alternativas de circulação e baias pelo envelope varrido;
4. reserva fila sem bloquear a via de passagem;
5. testa fluxo em sentido único, duplo ou outro layout permitido pela frota;
6. calcula corte, aterro, superfície proposta e capacidade de suporte;
7. projeta drenagem própria e saída estável;
8. mede área produtiva afetada e conflito com conservação;
9. calcula capacidade nominal e degradada;
10. registra obras, permissões e dados ainda pendentes.

O POA não precisa ser retangular. Sua geometria deve resultar dos acessos, envelopes, baias, fila, terreno e drenagem. Um local geometricamente grande pode ser inviável por não possuir rota de entrada/saída ou por concentrar água.

### 9.5 Roteamento

O motor executa caminho mínimo dependente de veículo, estado de carga e condição operacional. O custo de uma aresta pode incluir:

```text
custo_aresta = tempo
             + combustivel
             + desgaste
             + risco_de_compactacao
             + repeticao_de_passadas
             + conflito_de_trafego
             + penalidade_de_re
             + intervencao_e_manutencao
```

São proibidos arcos que violem barreiras, permissões, largura, giro, rampa, suporte, ponte, porteira ou drenagem. O custo carregado e vazio é calculado separadamente.

### 9.6 Localização capacitada de POAs

Uma formulação inicial usa:

- `x_j`: 1 quando o POA candidato `j` é selecionado;
- `y_ijt`: 1 quando a demanda `i`, na janela `t`, é atribuída ao POA `j`;
- `b_j`: quantidade/configuração de baias no POA `j`;
- `r_ij`: rota viável entre demanda `i` e POA `j`.

Objetivo multiobjetivo:

```text
min J = custo_de_implantacao_e_area
      + custo_de_transbordo_vazio_e_carregado
      + custo_rodoviario
      + custo_de_espera_da_colhedora
      + custo_de_fila_e_ociosidade
      + custo_de_trafego_e_compactacao
      + custo_de_intervencao_e_manutencao
      + penalidades_de_risco_nao_eliminatorio
```

Restrições mínimas:

- toda demanda possui um POA e uma rota viável;
- somente POA selecionado recebe demanda;
- capacidade temporal, de baia, fila e acesso é respeitada;
- veículos e rotas são compatíveis;
- metas de entrega e janelas operacionais são atendidas;
- área, orçamento e quantidade de POAs respeitam a rodada;
- nenhum hard gate participa como simples penalidade.

Para instâncias grandes, a solução pode usar MILP com decomposição, geração de colunas ou heurísticas, seguida sempre por validação independente.

### 9.7 Despacho e simulação

Depois da localização, um solver temporal atribui colhedoras, transbordos e caminhões aos eventos. A solução determinística é submetida a simulação de eventos discretos ou amostragem de cenários com:

- produtividade e densidade de carga variáveis;
- velocidades e tempos de transferência variáveis;
- falhas, manutenção, abastecimento e troca de turno;
- atraso ou falta de caminhão;
- degradação de via e cenário úmido;
- indisponibilidade de POA, baia ou acesso;
- mudança de meta da usina.

O simulador retorna distribuições de fila, espera, utilização, entrega e custo. Médias isoladas não aprovam o cenário.

### 9.8 Retorno ao gerador de sulcação

O processo é iterativo:

```text
sulcacao
 -> massa_e_load_events
 -> swap_windows
 -> POAs_e_rotas
 -> despacho_e_filas
 -> metricas_logisticas
 -> ajuste_de_tiros_saidas_carreadores_e_POAs
 -> nova_simulacao
```

O motor pode alterar direção, fase, sequência, portais de saída, quantidade de zonas ou posição de POAs. Não pode atravessar `power_barrier`, remover uma estrutura conservacionista sem recalcular a água ou criar um caminho de tráfego não autorizado.

## 10. Relação entre tiros longos e logística

A razão:

```text
R_tiro = massa_esperada_do_tiro / C_efetiva_do_transbordo
```

ajuda a identificar tiros que exigem múltiplas trocas, geram carga parcial ou deslocam o enchimento para trechos difíceis. Ela é uma métrica, não um alvo universal de valor inteiro.

Para cada tiro, o motor mede:

- quantidade e posição dos enchimentos;
- distância carregada até cada saída e POA;
- possibilidade de troca sem parar a colhedora;
- massa residual ao terminar a sequência;
- percurso para retornar à frente;
- passadas repetidas e invasão da cultura;
- interação com carreadores, terraços e barreiras;
- efeito sobre fila, frota e entrega à usina.

Tiros ligados entre talhões permanecem sujeitos às mesmas regras. Um portal autorizado pode manter a guia operacional, mas não elimina a necessidade de recalcular enchimentos, trocas e saídas. Se a ligação ou qualquer parte de sua manobra interceptar `power_barrier`, a conexão é quebrada e não existe crossing automático na V1.

## 11. Parâmetros e responsabilidades

### 11.1 Princípio

Parâmetros variáveis devem ser armazenados em perfis versionados. O usuário escolhe o perfil aplicável à rodada e altera apenas os parâmetros sob sua governança. Limites legais, de fabricante, ambientais, hidráulicos e de segurança não são controles de preferência.

Todo parâmetro registra:

- valor e unidade;
- escopo espacial e temporal;
- fonte e método;
- responsável;
- data e revisão;
- incerteza ou distribuição;
- status de aprovação.

### 11.2 Perfil permanente de frota

- capacidades de massa e volume;
- dimensões, eixos, pneus e cargas;
- altura e envelope operacional;
- raio, articulação e envelope varrido;
- rampas, inclinação lateral e quebra vertical admissíveis;
- velocidades por estado e superfície;
- tempos e modo de transferência;
- disponibilidade, custos e falhas.

### 11.3 Parâmetros da fazenda

- vias, sentidos, superfícies e restrições;
- POAs existentes e áreas permitidas;
- `power_line_axis` e `constraints.overhead_power_line_exclusion_half_width_m`, ou declaração de ausência;
- capacidade de suporte e política de umidade;
- drenagem, estruturas e áreas excluídas;
- portais, pontes, bueiros e porteiras;
- permissões de trânsito, obra e operação;
- acessos rodoviários e destinos possíveis.

### 11.4 Parâmetros da safra ou rodada

- produtividade e incerteza;
- variedade e condição da cultura;
- frente, frota disponível e reserva;
- turnos, pausas e manutenção;
- ordem e janela de colheita;
- frota rodoviária e distância/tempo à usina;
- meta horária e capacidade de recebimento;
- condição seca, úmida ou degradada.

### 11.5 Preferências configuráveis da geração

- permitir apenas POAs existentes ou também novos;
- permitir ou não novos acessos e carreadores;
- orçamento e quantidade máxima de POAs;
- tempo ou distância operacional máxima por classe de rota;
- nível de serviço de colhedora, transbordo e caminhão;
- política dedicada, compartilhada ou dinâmica;
- reserva mínima de equipamentos;
- limite de área produtiva afetada;
- prioridades de custo, intervenção, tráfego e robustez;
- talhões e fazendas que podem compartilhar recursos;
- liberdade para alterar sequência e direção da colheita.

Essas escolhas alteram o Pareto. Elas não desligam gates.

### 11.6 Parâmetros que não são sliders livres

- `constraints.overhead_power_line_exclusion_half_width_m` sem fonte aprovada;
- permissões e limites fundiários;
- envelope e limites do fabricante;
- restrições de vias, pontes e concessionárias;
- APP e exclusões ambientais;
- capacidade hidráulica e destino da água;
- capacidade de suporte aprovada do solo/piso;
- requisitos do responsável técnico.

## 12. Carteira de cenários

Cada macrocenário conservacionista elegível e cada modo operacional aplicável será avaliado com a carteira de POA:

| ID | Cenário | Semântica |
|---|---|---|
| `P0_POA_EXISTENTE` | baseline | usa POAs, vias, sequência e política atuais |
| `P1_SEM_NOVA_OBRA` | reorganização | seleciona e opera apenas áreas e acessos existentes elegíveis |
| `P2_NOVOS_POAS` | intervenção localizada | permite novos pátios e acessos dentro do escopo autorizado |
| `P3_INTEGRADO` | redesenho | otimiza conjuntamente sulcação, sequência, saídas, carreadores e POAs |

Cada estratégia é cruzada, depois dos gates, com perfis de objetivo independentes:

| Perfil | Prioridade de ranqueamento |
|---|---|
| `MIN_TRANSBORDO` | menor percurso e tempo vazio/carregado |
| `MIN_COMPACTACAO` | menor área única trafegada e repetição de carga |
| `BALANCEADO_CAPACITADO` | compromisso entre custo, serviço, área e intervenção |
| `ROBUSTO_PICO` | melhor margem nos cenários P90, falha e acesso degradado |

Estratégia de implantação e perfil de objetivo não devem ser fundidos em um único ID.

Políticas de despacho:

| ID | Política | Semântica |
|---|---|---|
| `D0_DEDICADO` | alocação fixa | recursos vinculados a uma colhedora ou frente |
| `D1_POOL_DINAMICO` | compartilhamento | recursos despachados conforme estado e previsão de demanda |
| `D2_POOL_COM_RESERVA` | robustez | pool dinâmico com capacidade de contingência parametrizada |

Condições mínimas de estresse:

- produção nominal;
- produtividade alta dentro da incerteza;
- produtividade espacial heterogênea;
- piso e velocidades degradados por umidade;
- indisponibilidade de um transbordo;
- atraso de caminhões;
- perda de uma baia ou acesso;
- mudança de turno ou manutenção.

O espaço fatorial completo pode ser grande. O motor elimina combinações inelegíveis, amostra configurações distintas e mantém representantes da fronteira de Pareto. `power_barrier` vale igualmente para todos os cenários.

## 13. Hard gates

| Código | Condição de reprovação |
|---|---|
| `POA_DATA_001` | falta dado obrigatório ou sua procedência |
| `POA_POWER_001` | POA, baia, fila, rota, manobra, troca ou sulco atravessa `power_barrier` |
| `POA_POWER_002` | não existe `power_line_axis` nem declaração válida de ausência |
| `POA_GEOM_001` | pátio ou acesso não contém o envelope completo da frota |
| `POA_ACCESS_001` | não existe rota vazia e carregada mecanizável |
| `POA_ROAD_001` | via, ponte, porteira ou acesso não suporta o conjunto |
| `POA_SOIL_001` | piso ou rota excede capacidade de suporte na condição avaliada |
| `POA_DRAIN_001` | pátio bloqueia o escoamento ou não possui descarga estável |
| `POA_ENV_001` | conflito com APP, água, área úmida ou exclusão ambiental |
| `POA_AUTH_001` | falta autorização de uso, trânsito, obra ou acesso |
| `POA_CAP_001` | capacidade física ou temporal é menor que a demanda requerida |
| `POA_QUEUE_001` | fila extrapola o polígono, bloqueia via ou cria conflito insolúvel |
| `POA_HARVEST_001` | nível de serviço da colhedora não é atendido |
| `POA_SWAP_001` | evento de enchimento não possui janela segura de troca |
| `POA_TRAFFIC_001` | rota invade cultura ou soqueira fora das faixas aprovadas |
| `POA_MILL_001` | plano não atende a entrega ou o destino disponível |
| `POA_MASS_001` | balanço de massa entre colheita, transbordo, caminhão e destino não fecha |

Cada reprovação informa geometria, período, veículo, evidência, impacto e ação corretiva. Um cenário bloqueado permanece no relatório como `NAO_APLICAVEL`; ele não é silenciosamente removido.

## 14. Métricas

### 14.1 Tiros, massa e trocas

- massa `P10/P50/P90` por segmento e tiro;
- quantidade de cargas completas e parciais;
- posição e incerteza dos `load_events`;
- quantidade de `swap_windows` válidas e inválidas;
- tempo de colhedora aguardando transbordo;
- tiros interrompidos por barreira e respectivo motivo.

### 14.2 Transbordos

- distância vazia, carregada e total;
- distância e tempo por tonelada;
- P50, P90, P95 e máximo do ciclo;
- tempo de enchimento, saída, viagem, fila, transferência e retorno;
- utilização, ociosidade, disponibilidade e reserva;
- combustível, horas, custo e emissões parametrizadas;
- quantidade de ré, manobras e conflitos.

### 14.3 POA

- área reservada, área produtiva perdida e área perturbada;
- quantidade e utilização das baias;
- throughput em cargas/h e t/h;
- fila média, P95 e máxima;
- tempo de espera de transbordos e caminhões;
- bloqueios de acesso e ocupação da via;
- corte, aterro, superfície, drenagem e custo;
- capacidade nominal e degradada;
- margem em relação a todas as exclusões.

### 14.4 Caminhões e usina

- distância, tempo de ciclo e custo por tonelada;
- utilização e espera por veículo;
- pontualidade e regularidade da entrega horária;
- desvio da meta de recebimento;
- tempo corte-destino;
- risco de falta e excesso de inventário em trânsito;
- filas no POA e no destino quando estiverem no escopo.

### 14.5 Tráfego, solo e cultura

- área única trafegada;
- número e intensidade de passadas por célula;
- distância carregada sobre cada classe de solo/via;
- carga por eixo e risco de compactação por condição de umidade;
- sobreposição do envelope com linha e soqueira;
- área danificada nas bordas e acessos;
- trilhas preferenciais e efeito sobre o escoamento;
- vida útil e manutenção esperada do piso e das vias.

### 14.6 Segurança e robustez

- distância mínima a `power_barrier` e violações iguais a zero;
- conflitos espaço-temporais entre veículos;
- utilização de ré e manobras críticas;
- cenários com fila fora da área reservada;
- probabilidade de parada da colhedora;
- desempenho com quebra, atraso, alta produtividade e condição úmida;
- pior cenário e percentis, além da média.

## 15. Modelo de persistência

```text
scenario
  harvest_logistics_plan
    harvest_front
    harvest_sequence
    yield_surface_ref
    fleet_profile_refs
    load_event_set
    swap_window_set
    road_graph_ref
    poa_plan
      poa_candidate
      poa_site
      poa_access
      transfer_bay
      queue_lane
    transshipment_route_set
    truck_route_set
    dispatch_policy
    mill_delivery_plan
    logistics_simulation
    traffic_intensity_surface_ref
  restriction_plan
    power_line_axis_ref
    overhead_power_line_exclusion_half_width_m
    power_absence_declaration_ref
    power_barrier_ref
```

Campos mínimos de `poa_site`:

- `poa_id`, `farm_id` e `legal_land_unit_id`;
- `geometry_ref` e `proposed_surface_ref`;
- estado `EXISTING`, `CANDIDATE`, `PROPOSED`, `APPROVED` ou `AS_BUILT`;
- acessos, sentidos, baias e fila;
- veículos compatíveis;
- capacidade nominal e degradada;
- drenagem e saída;
- permissões e revisão;
- QA, pendências e motivos de bloqueio.

Campos mínimos de `load_event`:

- `load_event_id`, tiro e estaqueamento;
- geometria `PointZ`;
- massa/volume e percentis;
- horário/janela;
- `swap_window_id`;
- POAs e rotas elegíveis;
- fonte da produtividade e perfil de capacidade.

## 16. Camadas de saída

| Camada | Geometria | Conteúdo |
|---|---|---|
| `power_line_axis` | `LineString` 2D | eixo declarado pelo centro dos postes |
| `power_barrier` | `Polygon` | buffer dissolvido usado como barreira absoluta |
| `broken_worked_segments` | `LineStringZ` | sulcos recortados e motivo da interrupção |
| `load_events` | `PointZ` | eventos e percentis de enchimento |
| `swap_windows` | `LineStringZ` ou `Polygon` | janelas de troca e estado de elegibilidade |
| `poa_candidates` | `Polygon` | alternativas, métricas e motivos de rejeição |
| `poa_selected` | `Polygon` | pátios selecionados e capacidade |
| `poa_layout` | geometrias mistas por camada | acessos, baias, faixas, fila e envelopes |
| `transshipment_routes_loaded` | `LineStringZ` | rotas carregadas por evento/período |
| `transshipment_routes_empty` | `LineStringZ` | retornos vazios |
| `truck_routes` | `LineStringZ` | percurso rodoviário e restrições |
| `traffic_intensity` | raster | passadas, carga e estado de carga por célula |
| `harvest_sequence` | tabela/grafo | ordem, horários, recursos e dependências |
| `logistics_metrics` | tabela | distribuição de desempenho e violações |

## 17. Níveis de entrega

| Nível | Insumos mínimos adicionais | Saída permitida |
|---|---|---|
| `E0_TRIAGEM` | terreno, limites, vias preliminares e situação da rede elétrica declarada | áreas candidatas, distâncias topográficas e lista de dados faltantes |
| `E1_OPERACIONAL` | E0 + produtividade/incerteza + frota completa + rede viária + POAs/áreas permitidas + tempos | eventos de carga, rotas dirigíveis, layout conceitual, frota e filas simuladas |
| `E2_CONSERVACIONISTA` | E1 + solo/suporte/umidade + drenagem + estruturas + superfície proposta | POAs e rotas recalculados com compactação, água, obras e cenários adversos |
| `E3_EXECUTIVO` | E2 + levantamento de campo + permissões + capacidade de suporte + tempos calibrados + responsável | projeto de locação, superfície, drenagem, layout, operação e exportação controlada |

Na V1, nenhum nível libera crossing automático de `power_barrier`. Em E3, o eixo, o buffer e sua fonte precisam estar aprovados ou substituídos por uma nova especificação formal de versão futura.

## 18. Validação e aceitação

### 18.1 Validação geométrica

- todos os POAs são polígonos válidos;
- baias, filas, manobras e rotas permanecem dentro das áreas permitidas;
- envelopes varridos não invadem cultura ou exclusões além da tolerância aprovada;
- não existe interseção com `power_barrier`;
- rotas possuem continuidade, sentido e veículo compatível.

### 18.2 Validação de massa

- massa produzida = massa transferida + inventário explícito + perdas parametrizadas;
- nenhuma carga excede capacidade efetiva;
- carga parcial e remanescente têm destino e regra;
- produtividade e capacidade informam fonte e incerteza.

### 18.3 Validação operacional

- simulação reproduz o plano determinístico em cenário sem variabilidade;
- telemetria calibra velocidades, tempos e falhas;
- fila física não excede `queue_lane`;
- nível de serviço é atendido nos percentis aprovados;
- o resultado é comparado ao baseline executado.

### 18.4 Validação agronômica e conservacionista

- tráfego permanece nas faixas planejadas;
- suporte do solo é testado por condição de umidade;
- superfície do POA e vias possuem drenagem própria;
- nenhuma trilha ou pátio cria receptor hidráulico acidental;
- vistoria confirma áreas úmidas, erosão, infraestrutura e interferências.

## 19. Evidência técnica

O [LOC da AgroAbdo](https://agroabdo.com.br/loc) confirma comercialmente a relevância de otimizar pátios de carregamento e tempos com produtividade, velocidades, capacidades e eficiências. A página pública é referência de problema e produto, não especificação do algoritmo proprietário.

Benedini e Conde, do Centro de Tecnologia Canavieira, incluem pátios de transferência, carreadores, manobras, formato dos talhões e sulcação no planejamento da base física. A publicação reforça que o pátio protege margens do canavial e que sua implantação deve considerar relevo e operação. As dimensões apresentadas no caso histórico não serão transformadas em valores universais do produto. [Sistematização de área para colheita mecanizada](https://aplacana.com.br/arquivo/2011/000014.pdf)

Dias formulou a alocação de pontos de transferência como problema de programação inteira sobre pontos de carga e distâncias em grafo. O estudo de caso encontrou redução de deslocamento dos tratores, demonstrando potencial da localização otimizada sem estabelecer promessa geral. [UFPR, otimização do deslocamento dos tratores transbordos](https://acervodigital.ufpr.br/xmlui/handle/1884/98218)

Mundim mostra que o dimensionamento do CTT é interdependente e sujeito a filas por variabilidade e desbalanceamento, justificando simulação de eventos discretos e políticas alternativas de despacho. [USP, dimensionamento de frota por simulação](https://teses.usp.br/teses/disponiveis/3/3148/tde-29062009-160037/en.html)

Lamsal, Jones e Thomas integram colheita e transporte, explorando atendimento de múltiplas localizações e variação da velocidade de colheita para coordenar oferta e processamento. [Sugarcane Harvest Logistics in Brazil](https://pubsonline.informs.org/doi/10.1287/trsc.2015.0650)

A Embrapa registra que o uso de transbordos mantém caminhões fora da área colhida e reduz a ocorrência de compactação, sustentando a separação entre rota interna e rota rodoviária. [Embrapa, carregamento da cana](https://www.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/cana-de-acucar/producao/planejamento-da-colheita/colheita/carregamento)

Passalaqua e Molin demonstram que erros de trajetória aumentam ao longo dos componentes de conjuntos articulados e são influenciados por curvas e inclinação lateral. Portanto o envelope do último implemento, e não apenas a linha do trator, deve validar rota, troca e POA. [Path errors in sugarcane transshipment trailers](https://repositorio.usp.br/item/002996222)

Esteban e colaboradores observaram alteração de propriedades físicas do solo sob configurações reais de transbordo, inclusive com tráfego controlado, reforçando a necessidade de medir passadas, carga, posição e umidade em vez de usar somente distância. [Effects of Infield Transshipment Traffic](https://www.mdpi.com/2624-7402/8/3/82)

Para a V1 elétrica, a orientação pública da Copel recomenda planejar dimensões de máquinas, manter carga e descarga afastadas das estruturas e não estacionar equipamentos sob a rede. O produto adota regra ainda mais simples de auditar: eixo 2D com buffer aprovado e ausência de crossing automático. [Segurança da energia na atividade rural](https://www.aen.pr.gov.br/Noticia/Copel-orienta-sobre-uso-seguro-da-energia-na-atividade-rural)

## 20. Critério de sucesso

O módulo estará pronto para piloto quando conseguir, de forma reproduzível:

1. transformar produtividade e tiros em eventos de enchimento com incerteza;
2. localizar janelas de troca sem pisoteio indevido;
3. gerar POAs como polígonos com layout, superfície, drenagem e capacidade;
4. rotear transbordos vazios e carregados no grafo real;
5. dimensionar POAs, baias, transbordos e caminhões por período;
6. simular filas, falhas, condição úmida e variação de produtividade;
7. devolver métricas logísticas ao gerador de sulcação;
8. comparar baseline, reorganização, novos POAs e redesenho integrado;
9. manter todas as operações fora de `power_barrier`;
10. explicar cada rejeição, hipótese, fonte e incerteza.

O resultado esperado não é “o POA mais próximo”. É uma carteira de projetos em que a colhedora, os transbordos, os caminhões, o solo, a água e a segurança funcionam como um único sistema verificável.
