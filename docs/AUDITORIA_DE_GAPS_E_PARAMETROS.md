# Auditoria de Gaps, Parametros e Interferencias

## 1. Conclusao executiva

A base conceitual do projeto esta correta: agua antes da operacao, separacao entre
linha-guia, sulco fisico, movimento de maquina e alcance hidraulico, comparacao
entre curva embutida, base larga/passante e ESD, e possibilidade de tiros entre
talhoes somente por portais verificados.

O produto, entretanto, ainda nao esta pronto para transformar uma solicitacao do
cliente em um cenario conservacionista liberado. Em 24/08/2026, o contrato
canonico, o catalogo de parametros e o inventario de aquisicao foram fechados.
Os gaps de prioridade zero remanescentes sao:

1. Interferencias ainda precisam estar completas e alterar area
   cultivavel, sulcos, alcances hidraulicos, portais, rotas e POAs.
2. POA e logistica de corte, transbordo e transporte possuem apenas um estagio
   estatico isolado; falta simulacao integrada.
3. Os scripts E0 ainda usam constantes locais e nao consomem um manifesto
   resolvido. Eles sao experimentos reproduziveis, nao o motor de producao.
4. PCE, PCX e os solvers C1, C2 e C3 continuam especificados, mas nao
   implementados.

O caminho recomendado e construir primeiro o resolvedor de parametros e o
catalogo de restricoes; em seguida, gerar familias completas e quebras por
interferencia; depois integrar POA estatico e roteamento; por ultimo, simular
despacho, filas e incerteza.

## 2. Decisao V1 para rede eletrica

Na primeira versao, o cliente podera enviar um vetor `LineString` denominado
`power_line_axis`, desenhado pelo centro dos postes. Ele representa o eixo
horizontal aproximado da rede, nao a geometria dos cabos.

O eixo sera usado como **barreira operacional absoluta**:

```text
power_line_axis
  -> buffer de exclusao com fonte registrada
  -> recorte da area cultivavel
  -> quebra de todo worked_segment que intercepta a faixa
  -> fim e reinicio de hydraulic_reach analisados separadamente
  -> quebra do operational_run
  -> exclusao de POA, fila, troca, carga, descarga e manobra
  -> proibicao de portal e direct shot atraves da faixa
```

Nesta V1 nao existe estado automatico `LIFT_CROSS` sob rede eletrica. Se a
barreira separar duas partes do mesmo alinhamento, o resultado sao dois tiros e
duas rotas. Uma versao futura podera avaliar travessia 3D somente com perfil de
condutor, flecha, tensao, regra da concessionaria, envelope de cada maquina e
procedimento aprovado.

O buffer nao tera um numero universal. O pedido deve referenciar a largura
fornecida pelo cliente/proprietario da rede ou por um `rule_pack` aprovado. O
usuario pode aumentar a margem, mas nao reduzir o minimo aplicavel.

### 2.1 Declaracao de completude

O inventario de energia usa `PROVIDED`, `DECLARED_NONE` ou `NOT_REVIEWED`. O
catalogo geral de interferencias usa `COMPLETE`, `PARTIAL` ou `NOT_REVIEWED`.
Um shape fornecido apenas para parte do escopo e `PROVIDED` para a rede, mas
mantem o catalogo geral `PARTIAL` e a area nao revisada fechada.

Ausencia de arquivo nunca significa ausencia de rede; somente `DECLARED_NONE`
possui essa semantica. Uma nuvem que preserve apenas pontos classificados como
solo nao registra de forma confiavel postes, cabos, arvores ou edificacoes e nao
pode validar essa declaracao. Da mesma forma, conectores geometricos E0 entre
talhoes permanecem `UNCONFIRMED` ate que superficie, portal, barreiras,
permissao, greide e envelope da frota sejam comprovados.

### 2.2 Campos minimos da camada

| Campo | Regra |
|---|---|
| `feature_id` | identificador estavel |
| `type` | `POWER_LINE_AXIS` |
| `geometry` | `LineString` ou `MultiLineString`, CRS metrico |
| `centerline_method` | centro dos postes, cadastro ou levantamento |
| `exclusion_half_width_m` | fato/rule-pack com fonte; sem default universal |
| `source_ref` | arquivo, levantamento ou documento |
| `survey_date` | data da verificacao |
| `accuracy_m` | incerteza horizontal, quando conhecida |
| `owner_or_operator_ref` | identificador da concessionaria/proprietario |
| `review_status` | estado de completude e revisao |

## 3. Catalogo de interferencias

Toda feicao recebe efeitos independentes por operacao: `planting_effect`,
`harvest_effect`, `transit_effect`, `earthwork_effect`, `hydraulic_effect` e
`poa_effect`. Um objeto pode permitir transito e proibir plantio; outro pode
permitir cultivo e proibir escavacao.

Efeitos canonicos:

```text
EXCLUDE | SPLIT_WORK | BLOCK_ROUTE | TRANSIT_ONLY | CROSS_AT_PORTAL
NO_STOP | NO_TURN | NO_LOAD | NO_EARTHWORK | HYDRAULIC_NODE
SEASONAL | LOAD_LIMITED | REVIEW_REQUIRED
```

| Grupo | Feicoes a aceitar | Consequencia principal |
|---|---|---|
| Energia | eixo aereo, postes, estais, servidao, rede subterranea | excluir, quebrar e bloquear POA/rota |
| Limites | divisa, arrendamento, cerca, muro, porteira | exigir permissao e portal real |
| Transporte | carreador, estrada publica, ferrovia, ponte, bueiro | validar acesso, carga, giro e drenagem |
| Hidraulica | terraco, CEV, canal, vala, talvegue, saida | criar no de transferencia ou bloquear |
| Ambiente | curso d'agua, nascente, APP, area umida, vegetacao protegida | exclusao e enquadramento profissional |
| Subsolo | gasoduto, oleoduto, agua, fibra, irrigacao | proibir escavacao sem cadastro/autorizacao |
| Agricultura | pivo, hidrante, carreta, trilha controlada | conflito dinamico e/ou sazonal |
| Fisico | arvore, rocha, edificio, silo, tanque, talude | manter, desviar ou remover com aprovacao |
| Instabilidade | ravina, vocoroca, erosao, solo mole, inundacao | reabilitar, limitar carga ou fechar |
| Seguranca | aerodromo, emergencia, incendio, manutencao | manter corredor livre |

O estado `UNKNOWN` e fechado. Uma feicao so deixa de atuar como barreira quando
existe classificacao, regra, evidencia, vigencia e aprovacao compatveis com a
operacao solicitada.

## 4. Parametros: quem decide o que

Todo valor deve carregar:

```text
value + unit + origin + source_ref + spatial_scope + temporal_scope
+ uncertainty + confidence + validity + entered_by + approved_by + revision
```

Origens aceitas:

```text
MEASURED_SURVEY | MEASURED_TELEMETRY | USER_INFORMED
USER_CONFIRMED_INFERENCE | EXTERNAL_DATA | CALCULATED
CATALOG | RULE_PACK | E0_ASSUMPTION
```

### 4.1 Fatos informados ou confirmados pelo usuario

- fazenda, unidade legal, talhoes, safra, operador e permissoes;
- espacamento e arranjo de linhas, variedade, manejo e produtividade;
- estruturas existentes, carreadores, estradas, portais e saidas;
- declaracao e shape de rede eletrica e demais interferencias;
- frota real, configuracoes, capacidades, velocidades e tempos;
- raio minimo da trajetoria durante trabalho e raio minimo de giro, informados
  separadamente por conjunto e operacao;
- POAs existentes, areas permitidas/proibidas e destino dos caminhoes;
- turnos, janelas de colheita, meta de entrega e restricoes operacionais;
- possibilidade de compartilhar tiros, rotas ou POA entre propriedades.

Esses dados sao fatos do projeto. O usuario pode corrigi-los com nova evidencia,
mas nao devem ser tratados como pesos livres do otimizador.

O catalogo separa `fleet.minimum_work_path_radius_m`, usado para validar a
curvatura da linha enquanto a maquina trabalha, de
`fleet.minimum_turn_radius_m`, usado para validar o giro em cabecalho, carreador
ou outra superficie de manobra confirmada. Eles descrevem movimentos diferentes,
nao sao intercambiaveis e um valor ausente nao pode ser inferido do outro.

### 4.2 Valores calculados

- MDT, declividade, curvatura, acumulacao, bacias e incerteza espacial;
- area operavel, buffers e conflitos entre geometrias;
- orientacao, fase, curvatura, greide, reversoes e cobertura dos sulcos;
- PCE/PCX, capacidade, armazenamento, velocidade e destino da agua;
- massa acumulada por tiro e pontos provaveis de enchimento;
- rotas dirigiveis, ciclos, filas, trafego e compactacao;
- locais candidatos e capacidade necessaria dos POAs;
- Pareto, sensibilidade, robustez e motivos de inelegibilidade.

Valores calculados nao sao editados diretamente. Uma alteracao deve ocorrer no
insumo, na regra ou no algoritmo e gerar nova revisao.

### 4.3 Regras tecnicas versionadas

- limites de solo, chuva, erosao, capacidade e receptores;
- geometria admissivel de terracos, canais, ESD e cruzamentos;
- limites do fabricante e envelope da frota;
- faixa de exclusao, servidao, autorizacao e regras de infraestrutura;
- criterios ambientais, fundiarios, viarios e de seguranca;
- niveis de qualidade e responsabilidade profissional.

Essas regras vivem em `rule_pack` com regiao, cultura, validade, fonte e
responsavel. Nao sao sliders e nunca viram simples pesos.

### 4.4 Preferencias do otimizador

Somente depois dos hard gates, o usuario pode escolher:

- perfil `CONSERVACAO`, `EQUILIBRIO` ou `OPERACAO`;
- prioridade por menos intervencao, menos manobras, menor trafego ou menor custo;
- numero maximo/orcamento de novos POAs e carreadores;
- preferencia por infraestrutura existente;
- estrategia de frota dedicada, pool ou pool com reserva;
- cenarios seco, umido, nominal e adverso a comparar.

O produto deve publicar representantes de Pareto. Uma media ponderada jamais
compensa descarga insegura, rede eletrica, falta de permissao ou candidato sem
saida hidraulica.

### 4.5 Parametros internos

Passo angular, tolerancia numerica, tamanho de celula, suavizacao, densidade de
amostragem, seed e tolerancia do solver sao auditaveis e versionados, mas nao
sao controles comuns do cliente.

## 5. Politica de defaults e overrides

Nao existe default de producao para:

- declaracao/afastamento de rede eletrica;
- permissao fundiaria, viaria, de obra ou transferencia de agua;
- chuva e tempo de retorno de projeto;
- infiltracao executiva, capacidade de receptor e suporte de solo;
- dimensoes criticas da frota, ponte, estrada, portal ou POA;
- limite ambiental ou destino de uma descarga.

Defaults permitidos sao conservadores e explicitos:

- limite ou obstaculo desconhecido fica fechado;
- portal sem evidencia fica bloqueado;
- parametro de experimento recebe origem `E0_ASSUMPTION` e sensibilidade;
- perfil de objetivo inicial pode ser `EQUILIBRIO`, sem alterar gates;
- dado de catalogo de maquina exige confirmacao da configuracao real.

Um override profissional registra regra original, valor substituto, escopo,
justificativa, evidencia, responsavel, atribuicao, validade, risco residual e
hash das dependencias. Override nao apaga a violacao anterior. CRS/datum
desconhecido, falta de permissao, energia nao revisada e saida hidraulica nao
resolvida nao sao liberados por aceite simples.

## 6. Dependencias e invalidacao

| Alteracao | Artefatos que devem ser invalidados |
|---|---|
| terreno ou datum | derivados, agua, estruturas, sulcos, rotas e POAs |
| shape/buffer eletrico | area operavel, quebras, portais, rotas e POAs |
| frota/configuracao | cabeceiras, curvatura, portais, trafego e POAs |
| espacamento | fase, cobertura, massa por tiro, trafego e logistica |
| produtividade | eventos de carga, ciclos, POAs, filas e despacho |
| solo/chuva/receptor | elegibilidade conservacionista e superficie proposta |
| permissao | conexoes, obra, transferencia e compartilhamento de POA |
| rule-pack | todos os gates e aprovacoes dependentes |

Cada cenario deve referenciar as revisoes de terreno, hidrologia, frota,
interferencias, pedido de geracao, regra e logistica que o produziram.

## 7. Gaps priorizados

### P0 - antes de chamar de anteprojeto

- contrato `project-generation-request` e catalogo de parametros;
- manifesto resolvido consumido pelo motor, sem constantes duplicadas;
- camada de restricoes com declaracao de completude;
- corte real de sulcos e rotas pelo `power_line_axis`;
- perfis de todas as maquinas do ciclo, nao um unico veiculo;
- eliminacao do fallback que escolhe candidato inviavel em E1+;
- validacao semantica de portais, permissoes e estados desconhecidos;
- POA como patio e grafo logistico, ao menos em triagem estatica.

### P1 - anteprojeto operacional condicionado

- familias curvas com espacamento fisico validado, campo integravel e continuidade sem parada interna;
- integracao real entre config, schema, gerador e relatorio;
- produtividade P10/P50/P90 e massa por tiro;
- rotas carregadas/vazias, POAs capacitados e mapa de trafego;
- superficie proposta, agua e logistica recalculadas em conjunto;
- testes positivos/negativos de schema e golden datasets.

### P2 - projeto e operacao supervisionada

- perfis 3D de redes e travessias especiais aprovadas;
- dimensionamento executivo, locacao e as-built;
- despacho temporal e simulacao de eventos discretos;
- telemetria para calibrar produtividade, tempos, filas e compactacao;
- monitoramento de implementacao e manutencao.

## 8. Gates novos

- `G12_INTERFERENCES_COMPLETE`: restricoes declaradas e revisadas.
- `G13_POWER_BARRIER_APPLIED`: eixo eletrico e buffer cortaram todas as camadas
  aplicaveis, ou existe `DECLARED_NONE` valido.
- `G14_FLEET_LIFECYCLE_VALID`: sulcacao, plantio, colheita, transbordo,
  transporte e terraplenagem foram verificados.
- `G15_POA_SITE_SAFE`: local, solo, drenagem, acesso, energia e permissoes
  aprovados.
- `G16_LOGISTICS_CAPACITY`: frota, baias, filas e entrega atendem a demanda sob
  variabilidade.
- `G17_NO_FEASIBLE_MEANS_NONE`: E1+ nao publica candidato reprovado como melhor.
- `G18_DTM_FULL_COVERAGE`: toda geometria util deve estar dentro do MDE e sobre
  celulas finitas; qualquer area fora do raster ou celula invalida/`NoData`
  reprova a geracao. O resultado deve registrar contagens e areas de cada
  ocorrencia.

## 9. Criterio de pronto

Um pedido so e reproduzivel quando:

1. todos os parametros usados estao resolvidos com unidade e origem;
2. toda restricao tem declaracao de completude e comportamento por operacao;
3. cada resultado referencia as revisoes que o geraram;
4. nenhuma continuidade operacional implica continuidade hidraulica;
5. cada quebra de sulco tem destino da agua e estado operacional explicitos;
6. cada POA tem poligono, acesso, capacidade, drenagem e permissao;
7. candidatos inviaveis aparecem como inviaveis, com motivo e acao corretiva;
8. uma nova revisao invalida deterministicamente todos os dependentes.

## 10. Referencias de seguranca

Contratos implementados nesta revisao:

- [`catalogo_parametros_projeto.json`](../config/catalogo_parametros_projeto.json): 151 parametros, 113 marcados como `safety_critical`, e politica de defaults;
- [`catalogo_aquisicao_insumos.json`](../config/catalogo_aquisicao_insumos.json): 26 pacotes de evidencia com fontes, protocolos, QA, validade e blockers;
- [`catalogo_capacidades_motor.json`](../config/catalogo_capacidades_motor.json): separacao entre capacidade implementada, limitada, especificada e inexistente;
- [`inventario_insumos_dataset_atual.json`](../config/inventario_insumos_dataset_atual.json): exemplo publico anonimizado de inventario;
- [`GUIA_AQUISICAO_INSUMOS_E_PARAMETROS.md`](GUIA_AQUISICAO_INSUMOS_E_PARAMETROS.md): estrategia completa de aquisicao e parametrizacao;
- [`project-generation-request.schema.json`](../schemas/project-generation-request.schema.json): pedido versionado de geracao;
- [`exemplo_pedido_e0_dataset_atual.json`](../config/exemplo_pedido_e0_dataset_atual.json): pedido ficticio, com bloqueios explicitos;
- [`conservation-scenario.schema.json`](../schemas/conservation-scenario.schema.json): resultado 2.1 com interferencias e POA;
- [`validate_project_inputs.py`](../scripts/validate_project_inputs.py): validador sem dependencia externa.
- [`poa-static-stage-manifest.schema.json`](../schemas/poa-static-stage-manifest.schema.json): contrato publicado do manifesto consumido por `optimize_static_poa.py`; `validate_request()` no script continua sendo a implementacao autoritativa.
- [`verify_sulcation_scenarios.py`](../scripts/verify_sulcation_scenarios.py): agora tambem exige `operational_run_id` presente, unico e igual ao `line_id` em E0 (antes de qualquer corte por barreira).

O exemplo atual deve retornar `CONSTRAINT_INVENTORY_INCOMPLETE` e
`OVERHEAD_POWER_LINE_NOT_REVIEWED`. Eles nao sao erros do arquivo; sao o
registro correto do que ainda falta para sair de E0.

A NR-31 e a referencia setorial para seguranca e saude no trabalho rural; a
NR-10 ajuda a estruturar zonas de risco eletrico, mas nao fornece sozinha um
buffer agricola universal. A regra final depende da concessionaria, do cadastro
e do responsavel tecnico.

- [NR-31, Ministerio do Trabalho e Emprego](https://www.gov.br/trabalho-e-emprego/pt-br/acesso-a-informacao/participacao-social/conselhos-e-orgaos-colegiados/comissao-tripartite-partitaria-permanente/normas-regulamentadora/normas-regulamentadoras-vigentes/norma-regulamentadora-no-31-nr-31)
- [NR-10, Ministerio do Trabalho e Emprego](https://www.gov.br/trabalho-e-emprego/pt-br/acesso-a-informacao/participacao-social/conselhos-e-orgaos-colegiados/comissao-tripartite-partitaria-permanente/arquivos/normas-regulamentadoras/nr-10.pdf)
- [Orientacao rural da Copel](https://www.aen.pr.gov.br/Noticia/Copel-orienta-sobre-uso-seguro-da-energia-na-atividade-rural)
