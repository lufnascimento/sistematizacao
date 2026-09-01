# Roadmap de Fechamento da Aderência Agronômica e Operacional

**Versão:** 1.0
**Data:** 20 de agosto de 2026
**Status:** sequência técnica de desenvolvimento e validação; não é cronograma comercial nem projeto executivo

## 1. Objetivo e fronteira atual

Este roadmap converte os gaps da [Matriz Agronômica de Conservação e Colheitabilidade](MATRIZ_AGRONOMICA_CONSERVACAO_COLHEITABILIDADE.md) em pacotes dependentes e gates de saída. Ele complementa o [Roadmap de Validação](ROADMAP_VALIDACAO.md), que continua sendo a referência para pilotos, equipe e testes de campo.

Capacidade executável hoje, em escopo limitado:

- CF0 gera e verifica família contínua **geométrica**, sem autorização hidráulica ou operacional;
- o estágio estático de POA faz localização/atribuição roteável em manifesto próprio, sem simulação integrada da frente;
- produtos do LAZ sustentam diagnóstico E0, não acurácia executiva sem checkpoints.

C1 curva embutida, C2 base larga/passante e C3 ESD estão `ESPECIFICADO`. Não existe solver C1, C2 ou C3 executável no produto atual. Nenhum validador de configuração muda esse fato.

## 2. Ordem de dependência

```text
WP0 fronteira e proveniência
  -> WP1 MDT, feições e incerteza
  -> WP2 padrão operacional de linhas
  -> WP3 frota, controlador, solo e colheitabilidade
  -> WP4 PCE + PCX + receptores
  -> WP5 C1 / WP6 C2 / WP7 C3
  -> WP8 POA + logística + cut-to-mill
  -> WP9 multi-cortes + O&M + incerteza integrada
  -> WP10 pilotos, as built e monitoramento
```

WP5, WP6 e WP7 podem ser desenvolvidos em paralelo depois de WP4, mas cada um conserva gates próprios. Otimização nunca antecipa nem compensa um gate de água, segurança, ambiente, solo ou máquina.

## 3. Pacotes de trabalho

| WP | Estado inicial | Escopo | Dependências e blockers | Entrega verificável | Gate de saída |
|---|---|---|---|---|---|
| `WP0` | `IMPLEMENTADO` parcial | congelar pedido, fontes, versões, hashes, rule-pack, parâmetros, permissões e limite de alegação | falta orquestração única entre estágios | manifesto canônico e matriz de proveniência | todo resultado aponta insumo, versão, autor e recorte de capacidade; ausência produz blocker explícito |
| `WP1` | `BLOQUEADO` para E2/E3 | classificar terreno/feições; validar datum, cobertura, checkpoints e incerteza do MDT; delimitar bacia além do talhão | checkpoints independentes, datum vertical, vazios/estruturas e montante/jusante | MDT aceito, mapa de resíduos/incerteza, feições classificadas e bacia completa | menor queda decisória excede a incerteza aprovada; 100% da área útil válida; regiões inconclusivas excluídas |
| `WP2` | `ESPECIFICADO` | emitir `guidance_line`, `worked_segment` e `movement_segment`; modelar `WORK/LIFT/CROSS/TURN`, barreiras, portais e divisa cadastral | WP1; superfícies e permissões | grafo operacional e padrão de linhas versionado | cada trecho possui estado, causa de quebra e permissão; nenhuma continuidade artificial sobre obstáculo ou propriedade |
| `WP3` | `ESPECIFICADO` | simular conjunto articulado, manobras, rampas 3D, perfil vertical, tráfego controlado, vazio/carregado, umidade/compactação e envelope do controlador | WP1-WP2; biblioteca real de frota, OEM/firmware, curva de suporte do solo | envelopes por eixo/estado, mapa de tráfego, gates de solo e pacote de teste do controlador | P95/máximo dentro dos corredores e limites OEM; linha/soqueira preservada; round-trip e ensaio de seguimento aprovados |
| `WP4` | `BLOQUEADO` | implementar PCE, PCX, receptores/CEV, eventos consecutivos, sedimento/obstrução e caminho de falha | WP1; chuva/IDF, solo/infiltração, seções, jusante e permissões | memória hidrológica/hidráulica reproduzível e rede de receptores | balanço de massa fecha; capacidade, velocidade/tensão, bordo livre e falha passam no envelope nominal/adverso |
| `WP5` | `ESPECIFICADO` | solver C1 embutida com variantes TI/TD e MDT proposto | WP2-WP4; seção/tolerância construtiva e receptor | candidatos C1 com geometrias, cortes/aterros, hydraulic reaches e memorial | somente candidatos aprovados em PCE/PCX, construção, colheitabilidade e receptor são selecionáveis |
| `WP6` | `ESPECIFICADO` | solver C2 base larga/passante TI/TD com nós explícitos de travessia | WP2-WP4; estado novo/degradado, recalque, trilhas, frota e umidade | candidatos C2 e mapa de travessias/fechamentos | capacidade hidráulica e trafegabilidade passam no estado degradado; “passante” é provado por combinação de frota e condição |
| `WP7` | `ESPECIFICADO` | solver C3 ESD setorial/complementado por família completa de sulcos | WP2-WP4; chuva-excesso por alcance, contribuição externa, convergência e destino | rede ESD, indicadores de difusão e mapa de sobrecarga/falha | todos os alcances fecham água e destino; nenhuma concentração não dimensionada; CEV aplicado conforme rule-pack, não por presunção nacional |
| `WP8` | `ESPECIFICADO` | integrar sequência de colheita, eventos de carga, transbordos, POAs, caminhões, balança, pátio e moagem | WP2-WP3 e candidatos aprovados de WP5-WP7; telemetria/tempos locais | simulador de eventos, Pareto e painel de cut-to-mill por lote | massa sem destino igual a zero; capacidade/filas/SLA aprovados; vazio/carregado separados; efeito de qualidade validado ou `NOT_EVALUATED` |
| `WP9` | `ESPECIFICADO` | avaliar cana-planta, soqueiras, reforma, degradação, manutenção, custo e incerteza conjunta | WP3-WP8; histórico de campo, inspeções e custos | cenário de ciclo de vida e plano O&M | gates permanecem válidos no estado degradado; plano de inspeção/reparo financiável; ranking estável no envelope de incerteza |
| `WP10` | `BLOQUEADO` | executar pilotos supervisionados, locação, controlador, chuva, colheita, as built e realimentação | WP1-WP9 e responsável técnico | golden datasets, relatórios de desvio, as built e critérios recalibrados | repetibilidade, segurança e desempenho demonstrados em ambientes distintos; exceções e faixa de aplicação publicadas |

## 4. Parâmetros e autoria

### 4.1 Fornecidos ou aprovados pelo cliente

- limites e permissões dos talhões, propriedades e portais;
- rede elétrica em eixo de postes/condutores e buffer aprovado, demais obstáculos e áreas proibidas;
- espaçamento agronômico e frota real, inclusive modelos, composições, pneus, cargas, displays e firmware;
- candidatos/restrições de POA, turnos, capacidade de colheita, transbordos, caminhões e SLA da usina;
- horizonte de cortes, política de manutenção, perfil de decisão e risco residual aceito.

### 4.2 Medidos ou calibrados tecnicamente

- datum, checkpoints, incerteza do MDT, classificação de feições e microbacia;
- solo por horizonte, infiltração/condutividade, erodibilidade, cobertura, umidade e capacidade de suporte;
- IDF/distribuição temporal, Tc, seções, rugosidade, sedimento, obstrução e receptores;
- envelope cinemático, offtracking, erro do controlador e comportamento por estado de carga;
- tempos operacionais, produtividade, filas, qualidade da matéria-prima e deterioração local;
- degradação, recalque, assoreamento, custo, frequência e eficácia da manutenção.

### 4.3 Nunca tratados como seletor livre

Conservação de massa, geometria do levantamento, observação de campo, limite OEM e exigência legal não viram pesos. Valores de artigo ou projeto de terceiros só entram como hipótese de sensibilidade identificada; não se tornam default nacional. O usuário pode escolher objetivos e nível de risco dentro do domínio tecnicamente aprovado, mas não desligar um gate.

## 5. Critérios transversais de aceite

| Gate | Pergunta de aceite | Evidência mínima |
|---|---|---|
| `G0_PROVENANCE` | é possível reproduzir exatamente o pedido e o resultado? | manifesto, hashes, versões, parâmetros, unidades, autores e rule-pack |
| `G1_TERRAIN` | a topografia resolve a menor decisão de cota/greide? | datum, checkpoints independentes, resíduos e mapa de incerteza |
| `G2_LINE_STATE` | trabalho, cruzamento, levantamento e manobra são inequívocos? | objetos separados, sequência, portais, barreiras e permissões |
| `G3_MACHINE_SOIL` | toda a composição opera sem invadir cultura nem exceder máquina/solo? | envelopes 3D, vazio/carregado, controlador, umidade e tráfego controlado |
| `G4_WATER` | toda água possui contribuição, capacidade, destino e caminho de falha? | PCE/PCX, seções, receptores, eventos e balanço de massa |
| `G5_SCENARIO` | C1, C2 ou C3 cumpre suas regras próprias? | memorial do cenário, MDT proposto, métricas e blockers zerados |
| `G6_LOGISTICS` | a frente entrega massa e qualidade sem instabilidade operacional? | eventos, rotas, filas P95, capacidade, cut-to-mill e sensibilidade |
| `G7_LIFECYCLE` | a alternativa permanece válida após uso e degradação? | horizonte multi-cortes, O&M, estado degradado e custo de ciclo |
| `G8_FIELD` | desenho, máquina e estrutura correspondem ao mundo real? | piloto, locação, as built, chuva/colheita monitoradas e aprovação profissional |

## 6. Primeira sequência de implementação

1. Fechar WP1 com checkpoints, datum, classificação de vazios/feições e bacia externa.
2. Implementar WP2 antes de exportar qualquer “linha longa”; o estado do implemento faz parte do produto.
3. Construir a biblioteca mínima de WP3 com uma plantadora/sulcador, uma colhedora e uma composição real de transbordo, em vazio e carregado.
4. Implementar WP4 como serviço comum; C1-C3 não devem possuir hidrologias incompatíveis entre si.
5. Entregar C1, C2 e C3 como solvers separados sobre o mesmo contrato de PCE/PCX e receptores.
6. Integrar o POA estático ao motor de eventos apenas depois de existirem segmentos e rotas aprovados.
7. Adicionar cut-to-mill inicialmente como SLA temporal; ativar efeito sobre qualidade somente após calibração local.
8. Rodar ciclo de vida e pilotos antes de qualquer alegação E3 ou exportação operacional em escala.

## 7. Referências de projeto

As referências primárias e oficiais, seu uso permitido e as proibições de generalização estão registradas na [seção 10 da matriz](MATRIZ_AGRONOMICA_CONSERVACAO_COLHEITABILIDADE.md#10-referências-primárias-e-oficiais-que-sustentam-os-gates). Em especial, o roteiro segue a integração solo-relevo-água-mecanização da [Embrapa](https://ainfo.cnptia.embrapa.br/digital/bitstream/item/136221/1/2015AP19.pdf), mantém a hidráulica separada da geometria e trata coordenação corte-transporte-moagem como problema integrado, conforme [Lamsal, Jones e Thomas (2016)](https://doi.org/10.1287/trsc.2015.0650).
