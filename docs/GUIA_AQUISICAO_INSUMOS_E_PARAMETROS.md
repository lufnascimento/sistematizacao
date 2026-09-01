# Guia de Aquisicao de Insumos e Parametros

> A rota configuravel para cada parametro, incluindo defaults seguros, metodos regionais, modelos OEM e valor proprio do cliente, esta em [BIBLIOTECA_PRESETS_MODELOS_REAIS_CANA.md](./BIBLIOTECA_PRESETS_MODELOS_REAIS_CANA.md). Presets orientam a coleta e a triagem; nao alteram o inventario de evidencias nem liberam E1, E2 ou E3.

## 1. Resposta executiva

O projeto atual nao possui um unico "dado faltante". Ele possui quatro tipos de
lacuna que o sistema precisa tratar de forma diferente:

1. `PROJECT_INPUT_MISSING`: o cliente, campo, laboratorio, telemetria ou OEM
   ainda nao forneceu o fato do projeto;
2. `CALIBRATION_MISSING`: o metodo e conhecido, mas falta selecionar, calibrar e
   aprovar a regra regional/local;
3. `FIELD_VALIDATION_MISSING`: existe um prior ou produto de triagem, mas falta
   confronto independente, vistoria, ensaio ou as built;
4. `LEGAL_APPROVAL_MISSING`: falta direito, licenca, outorga, autorizacao ou
   responsabilidade valida para a geometria e a acao.

Ha ainda uma quinta classe, separada de dados: `SOLVER_NOT_IMPLEMENTED`. Fazer
upload de mais arquivos nao cria um motor PCE, PCX, C1, C2, C3, frota 3D ou
logistica integrada.

O inventario de 24/08/2026 encontrou:

| Item | Quantidade |
|---|---:|
| parametros totais no catalogo 1.2 | 151 |
| parametros externos, fatos ou regras | 129 |
| parametros externos ainda nao resolvidos | 116 |
| pacotes de evidencia | 26 |
| disponivel apenas para screening | 1 |
| parcial | 6 |
| ausente | 19 |
| capacidade de motor implementada e verificada | 2 |
| capacidade implementada, mas limitada/isolada | 1 |
| apenas especificada | 8 |
| nao implementada | 4 |
| bloqueada pela falta de evidencia | 2 |

Conclusao de prontidao: `E0_TRIAGEM = READY`; `E1`, `E2` e `E3 = BLOCKED`.
Isso nao e uma falha do auditor. E o comportamento fail-closed necessario para
nao transformar uma nuvem de pontos em projeto hidraulico ou guia de maquina
sem evidencias.

## 2. O que um conjunto E0 deve conter

Um conjunto apto a triagem topografica E0 deve documentar:

- quantidade, classes e densidade espacial dos pontos LAS/LAZ;
- cobertura raster valida sobre toda a area util;
- MDT, declividade, curvas, densidade e fluxo topografico preliminar;
- limites e identificadores opacos dos talhoes;
- familias geometricas E0/CF0 e triagem de oportunidades entre talhoes;
- dados suficientes para deixar explicitos os bloqueios de POA e logistica.

Esses fatos possuem limites importantes:

- uma nuvem contendo apenas classe 2 nao permite auditar postes, cabos, arvores,
  edificacoes ou pontos rejeitados;
- data, referencia vertical e checkpoints independentes sao obrigatorios para
  promover resultados acima de E0;
- comparacao entre superficies derivadas da mesma fonte mede consistencia, nao
  acuracia absoluta;
- o limite de talhoes pode nao fechar a bacia contribuinte;
- o MDT nao foi condicionado com estradas, bueiros, canais, valas ou terracos
  verificados;
- as linhas atuais permanecem `HYDRAULIC_UNCONFIRMED` e nao sao C1, C2, C3 ou
  guidance.

## 3. Inventario do que falta

| Pacote | Estado atual | O que falta de fato | Como obter |
|---|---|---|---|
| escopo, limite e permissao | parcial | identidade legal do imovel e permissoes espacializadas | cliente + SIGEF/SICAR + revisao legal |
| controle vertical | parcial | datum, manifesto e checkpoints independentes | GNSS/estacao total ligada ao SGB |
| superficie e contexto | screening | breaklines, feicoes nao solo e envelope complementar | classificacao original/ortomosaico + campo |
| configuracao agronomica | parcial | espacamento confirmado, variedade, ciclo, arranjo e trafego por zona | plano agricola + conferencia em campo |
| produtividade e carga | ausente | P10/P50/P90, fluxo de massa e balanco de carga | historico + monitor/celula + balanca |
| solo fisico-hidraulico | ausente | perfil, horizonte, textura, densidade, Ksat, infiltracao, erosao | campanha estratificada + laboratorio |
| solo operacional | ausente | umidade, penetracao, suporte e precompressao correlacionados | campanha por posicao/estado operacional |
| chuva de projeto | ausente | IDF selecionada, TR, duracao, hietograma e antecedencia | SGB/ANA/INMET + aprovacao hidrologica |
| bacia completa | parcial | montante, entradas de borda, jusante e receptor | BHO/DEM para screening + topografia dirigida |
| drenagem e receptores | ausente | censo, perfis, secoes, bueiros, CEV, estado e falha | inventario de 100% dos ativos |
| rule-pack conservacionista | parcial | pack regional executavel e calibrado | fontes regionais + responsaveis tecnicos |
| subsuperficie | ausente | piezometria, surgencias, lencol e descarga | diagnostico hidrogeologico sazonal |
| identidade/geometria da frota | ausente | composicao e geometria 3D reais | cadastro + OEM + medicao local |
| carga/estabilidade da frota | ausente | eixos, pneus, pressao, CG, frenagem e rampas | balanca/ensaio + limites OEM aprovados |
| WORK/TURN | ausente | raios, offtracking, tail swing e envelope completo | ensaio GNSS do conjunto articulado |
| controlador/guidance | ausente | display, firmware, formato, round trip e tracking | OEM + simulador + ensaio na maquina |
| performance/confiabilidade | ausente | distribuicoes de ciclo, capacidade, consumo, falha e custo | telemetria + OS + ERP |
| interferencias gerais | ausente | inventario censitario e efeito por operacao | levantamento multidisciplinar |
| energia | ausente | shape dos postes/eixo ou declaracao espacial, censo e buffer aprovado | cliente/concessionaria + campo |
| ambiente/legal | ausente | hidrografia reconciliada, APP real e permissoes | bases oficiais + campo + orgao competente |
| portais/conexoes | ausente | portal, piso, largura, greide, drenagem e permissao | vistoria + envelope da frota |
| POA | ausente | candidatos, geotecnia, drenagem, layout, giro, fila e capacidade | topografia/geotecnia + operacao |
| rede logistica | ausente | grafo, obras, capacidade, estado e AET | levantamento viario + documentos |
| ciclo CTT | ausente | eventos, frota, despacho, filas, falhas e usina | telemetria + balanca + MES/TMS |
| cut-to-mill | ausente | timestamps, lote, massa, laboratorio e SLA local | integracao de sistemas + validacao local |
| QA e ciclo de vida | parcial | aprovacoes, piloto, as built, O&M e custo por safra | fluxo tecnico + campo apos implantacao |

O detalhamento de campos, unidades, formatos, protocolos, validade, responsaveis,
fontes e blockers de cada linha esta no
[`catalogo_aquisicao_insumos.json`](../config/catalogo_aquisicao_insumos.json).

## 4. Como isso vira sistema

Nem tudo deve aparecer como um campo livre. O sistema precisa manter cinco
classes:

| Classe | Quem resolve | Exemplo | Pode ser slider? |
|---|---|---|---|
| `USER_FACT` | cliente, campo, laboratorio, telemetria ou OEM | shape da rede, Ksat medido, frota real | nao |
| `RULE_PACK` | responsavel tecnico e fonte versionada | IDF adotada, secao/greide admissivel | nao |
| `CALCULATED` | motor reproduzivel | bacia, vazao, envelope, Pareto | nao editavel |
| `OPTIMIZER` | usuario apos hard gates | preferir tiro, custo ou menos trafego | sim, entre aprovados |
| `E0_ASSUMPTION` | sistema em triagem | velocidade ou produtividade proxy | somente E0 |

Todo valor ou referencia deve carregar, no minimo:

```text
parameter_id + value/ref + unit + source_kind + source_ref
+ captured_at + valid_from/to + method_ref + CRS + vertical_ref
+ uncertainty + responsible + reviewer + revision + hash
+ qa_status + applicability_geometry
```

Estados recomendados para a vida do dado:

```text
MISSING -> ESTIMATED_PRIOR -> DECLARED/MEASURED
-> CALIBRATED -> VALIDATED -> APPROVED -> EXPIRED
```

`ESTIMATED_PRIOR` e `DECLARED` podem ajudar uma simulacao de sensibilidade, mas
nao substituem `MEASURED`, `VALIDATED` ou `APPROVED` quando o gate os exige.

## 5. O que o sistema pode buscar automaticamente

O backend pode pre-preencher contexto e propor uma campanha usando fontes
oficiais. Nenhuma delas deve promover o projeto automaticamente para E2/E3.

| Tema | Fonte oficial | Uso permitido |
|---|---|---|
| limites rurais | [Meu Imovel Rural](https://www.gov.br/gestao/pt-br/assuntos/meu-imovel-rural/como-funciona-1/visualizar-dados-e-baixar-documentos-de-imoveis-rurais/) e [SIGEF](https://sigef.incra.gov.br/documentos/manual/) | reconciliar poligonos e documentos |
| temas CAR | [API SICAR](https://www.gov.br/conecta/catalogo/apis/sicar-tema/imovel-tema.yaml/swagger_view) | screening ambiental declaratorio |
| referencia vertical | [IBGE hgeoHNOR2020](https://www.ibge.gov.br/geociencias/modelos-digitais-de-superficie/modelos-digitais-de-superficie/31283-hgeohnor2020-modeloconversaoaltitudesgeometricasgnss-datumverticalsgb.html) | registrar transformacao e versao |
| solo regional | [PronaSolos](https://www.embrapa.br/en/busca-de-solucoes-tecnologicas/-/produto-servico/9076/portal-de-dados-da-plataforma-tecnologica-pronasolos-em-ambiente-sigweb) | estratificar campanha, nunca prescrever Ksat local |
| metodos de solo | [Embrapa](https://ainfo.cnptia.embrapa.br/digital/bitstream/item/181717/1/Manual-de-Metodos-de-Analise-de-Solo-2017.pdf) | versionar protocolo de laboratorio |
| chuva intensa | [Atlas IDF/SGB](https://rigeo.sgb.gov.br/items/ca0aefe1-91de-48b4-8810-b8517907ceb1) | descobrir equacoes candidatas |
| series hidrologicas | [HidroWeb/ANA](https://www.gov.br/ana/pt-br/assuntos/monitoramento-e-eventos-criticos/monitoramento-hidrologico/orientacoes-manuais/manuais-de-sistemas-e-servicos-de-disponibilizacao-de-dados-hidrologicos) | chuva, nivel, vazao e sedimento |
| clima diario | [INMET/BDMEP](https://portal.inmet.gov.br/servicos/bdmep-dados-historicos) | contexto e consistencia, nao hietograma sub-horario |
| bacia regional | [BHO/ANA](https://www.gov.br/ana/pt-br/assuntos/noticias-e-eventos/noticias/base-de-dados-geoespaciais-da-ana-permite-gestao-mais-eficiente-das-bacias-hidrograficas/) | descobrir montante e jusante |
| linhas de transmissao | [ANEEL/GCEM](https://dadosabertos.aneel.gov.br/dataset/gcem-gestao-de-informacoes-de-campos-eletromagneticos) | screening nacional, nao rede local completa |

A API HydroWeb atual possui manual publicado em 2026. A integracao deve guardar
codigo da estacao, periodo, nivel de consistencia, data de consulta e a serie
bruta importada. Para a chuva de projeto, a automacao seleciona candidatas; o
engenheiro aprova equacao, dominio, TR, duracao, hietograma e cenarios.

## 6. O que exige campo ou ensaio

Nao pode ser resolvido apenas pela internet:

- datum/checkpoints e incerteza real do MDT;
- relevo fino da bacia, breaklines, canais, estradas, bueiros e receptores;
- perfil, Ksat/infiltracao, erodibilidade, umidade, suporte e compactacao;
- completude de energia, nascentes, areas umidas e obstaculos;
- frota/configuracao, carga por eixo, raio WORK, raio TURN e envelope varrido;
- comportamento do controlador na composicao, relevo e firmware reais;
- POA, piso, drenagem, fila, giro e acessos;
- velocidades, ciclos, falhas, despacho e filas locais;
- as built, degradacao e manutencao.

Nao deve existir uma grade universal de amostragem. A campanha deve estratificar
solo, vertente, curvatura, manejo e trafego; forcar pontos em convergencias,
estradas, canais, cortes/aterros e receptores; executar piloto; e aumentar a
densidade segundo variabilidade e confianca. Para infiltracao, tres repeticoes
por unidade representativa e um minimo defensavel de piloto, nao default
executivo. Energia, APP, bueiros e receptores exigem censo, nao amostra.

## 7. Regra para C1, C2 e C3

### C1, curva embutida

Curva embutida descreve geometria. Ela ainda precisa declarar funcao TI ou TD.
O solver deve fechar secao, corte/aterro, volume/capacidade, bordo livre,
espacamento, infiltracao/conducao, receptor, estado as built e degradado.

### C2, base larga/passante

Base larga/passante adiciona trafegabilidade e implantacao. Alem de C1, exige
envelope da frota, carga por eixo, umidade, suporte, trilha de roda, recalque e
nos de cruzamento. "Passante" nunca e propriedade visual da secao.

### C3, ESD

ESD nao e um canal ou linha colorida. E uma rede composta por:

- familia completa de sulcos em desnível controlado;
- entrada lateral e contribuicao externa por trecho;
- PCE e PCX;
- drenagem de carreadores, transferencias e CEV/receptor quando aplicavel;
- controle de trafego e plano de colheita;
- destino e caminho de falha para cada alcance.

O campo axial/eikonal gera somente a geometria candidata. Depois o motor deve
segmentar, calcular chuva-excesso, propagar vazao, detectar convergencias,
dimensionar conexoes e testar estados novo, as built e degradado.

O [IAC BT 216](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf) e uma
base numerica importante para cana em Sao Paulo e separa PCE de PCX. O
[manual ANA](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5)
ajuda a estruturar o processo e ESD. Valores dessas fontes pertencem a um
`rule-pack` identificado; nao devem virar default nacional.

## 8. O que nao pode ter default universal

- TI, TD, ESD ou combinacao;
- grupo hidrologico, Ksat, infiltracao, CN, coeficiente C ou Green-Ampt;
- IDF, TR, duracao, hietograma e chuva antecedente;
- espacamento de terraco e alcance/comprimento maximo;
- greide de sulco, terraco, ESD, CEV ou estrada;
- secao, taludes, volume, bordo livre e rugosidade de Manning;
- velocidade/tensao admissivel e sedimento/obstrucao;
- necessidade/posicao de CEV e capacidade de receptor;
- limites de declividade, cruzamento ou passabilidade;
- raio, bitola, altura, estabilidade e clearance da frota;
- buffer eletrico;
- densidade de amostragem;
- tolerancia construtiva e fator de degradacao;
- SLA corte-moagem ou curva de perda de qualidade;
- pesos economicos que tentem compensar um hard gate.

O sistema pode sugerir um valor vindo de um `rule-pack`, mas deve mostrar fonte,
regiao, edicao, dominio, revisao e responsavel. Sem isso, o valor permanece
`MISSING`.

## 9. Sequencia de aquisicao recomendada

### Lote A, fechar E1 operacional

1. Confirmar escopo, propriedade, permissoes e configuracao agronomica.
2. Executar inventario censitario de interferencias, inclusive rede eletrica.
3. Levantar portais, carreadores e superficies WORK/LIFT/CROSS/TURN.
4. Cadastrar e medir a frota; executar ensaios WORK/TURN.
5. Levantar rede viaria, POAs existentes e produtividade historica.

### Lote B, permitir calculo E2

1. Fechar controle vertical e incerteza do MDT.
2. Completar a bacia a montante e o caminho a jusante.
3. Executar solo fisico-hidraulico e solo operacional.
4. Selecionar chuva de projeto e eventos adversos.
5. Inventariar drenagem/receptores e criar o rule-pack regional.
6. Implementar e validar PCE, PCX, C1, C2 e C3.

### Lote C, fechar E3

1. Integrar controlador/modelo/firmware e executar round trip.
2. Pilotar os cenarios selecionados e medir comportamento real.
3. Levantar as built e recalcular PCX com a geometria construida.
4. Aprovar dominios, permissoes, risco residual e responsabilidade tecnica.
5. Publicar O&M, estados degradados, gatilhos de fechamento e custo de ciclo.

## 10. Artefatos executaveis

- [`catalogo_parametros_projeto.json`](../config/catalogo_parametros_projeto.json):
  151 parametros, classe, unidade, autoridade, requisito e politica de default;
- [`catalogo_aquisicao_insumos.json`](../config/catalogo_aquisicao_insumos.json):
  26 pacotes com fontes, formatos, campos, protocolo, QA, validade e blockers;
- [`catalogo_capacidades_motor.json`](../config/catalogo_capacidades_motor.json):
  separa implementado, limitado, especificado e inexistente;
- [`inventario_insumos_dataset_atual.json`](../config/inventario_insumos_dataset_atual.json):
  exemplo publico anonimizado de inventario e proximas acoes;
- [`audit_project_readiness.py`](../scripts/audit_project_readiness.py):
  valida cobertura e gera prontidao sem inventar valores;
- `dataset/derived/input_readiness_report.json`: relatorio local calculado de
  gaps por nivel, cenario, evidencia e motor; nao e publicado no Git.

Comando de auditoria:

```powershell
python .\scripts\audit_project_readiness.py
```

O criterio final e simples: dado publico pode preencher triagem; documento OEM
pode preencher identidade e limite nominal; cliente pode declarar fato; campo
pode medir; responsavel pode aprovar; motor pode calcular. Nenhum desses papeis
substitui silenciosamente o outro.
