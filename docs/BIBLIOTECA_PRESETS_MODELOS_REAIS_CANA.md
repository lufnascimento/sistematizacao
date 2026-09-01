# Biblioteca de presets e modelos reais para cana

## Objetivo

Esta biblioteca fecha a lacuna entre um parametro vazio e a coleta do dado real. Para cada parametro configuravel, o produto passa a oferecer uma ou mais rotas:

1. bloqueio seguro do sistema;
2. valor publicado para sensibilidade E0;
3. busca em fonte oficial;
4. protocolo ou formulario de aquisicao;
5. metodo regional selecionavel;
6. ficha nominal de equipamento real;
7. valor proprio do cliente, com unidade, schema e proveniencia.

Um preset define como iniciar, coletar ou bloquear. Ele nao comprova o valor do projeto, nao substitui vistoria e nao aprova C1, C2, C3, POA, continuidade ou exportacao para maquina.

## Resultado implementado

| Item | Quantidade | Regra |
|---|---:|---|
| Parametros do contrato | 151 | Fonte unica de tipos, unidades, autoridade e gates |
| Parametros configuraveis | 139 | Todos aceitam valor proprio validado |
| Fatos/regras externos | 129 | Continuam dependentes de evidencia do projeto |
| Calculados | 12 | Nunca sao preenchidos por preset |
| Pacotes de evidencia | 26 | Todos possuem perfil de selecao |
| Fontes registradas | 48 | Oficial, metodo, censo, lei, pesquisa, OEM ou contrato interno |
| Modelos selecionaveis | 65 | Metodo, equipamento, protocolo, layout, fonte ou bloqueio |
| Evidencias resolvidas automaticamente | 0 | Preset nao altera prontidao do projeto |

Distribuicao do comportamento padrao:

| Comportamento | Parametros | Significado |
|---|---:|---|
| `FAIL_CLOSED` | 17 | Valor seguro exatamente igual ao contrato central |
| `E0_VALUE` | 7 | Numero publicado, visivel e limitado a triagem |
| `REFERENCE_OR_TEMPLATE` | 66 | Fonte, metodo, formulario ou protocolo a completar |
| `FAIL_CLOSED_OR_LOCAL_INPUT_TEMPLATE` | 27 | Permanece bloqueado ate classificacao ou dado local |
| `LOCAL_INPUT_TEMPLATE` | 22 | Nao existe numero defensavel para preencher |

## O que significa padrao do sistema

`SYSTEM_DEFAULT` nao significa "media de mercado" nem "recomendacao agronomica". O significado depende do tipo do modelo:

| Tipo | Exemplo | Efeito |
|---|---|---|
| `FAIL_CLOSED_POLICY` | rede eletrica `NOT_REVIEWED` | Bloqueia continuidade e liberacao |
| `OFFICIAL_DATA_SOURCE` | IDF SGB, BHO6 ANA, Conab | Prefill ou sensibilidade E0 |
| `OFFICIAL_METHOD` | IAC BT216, BT71, HidroTerraco | Metodo selecionado, ainda sem fatos locais |
| `REGULATORY_GOVERNANCE` | SAA/SP, Prosolo/PR | Overlay legal a revisar |
| `RESEARCH_PROTOCOL` | Embrapa Docs. 480 e 483 | Campanha ou inspecao, sem copiar os numeros do estudo |
| `PROCESS_ONLY` | ESD nacional | Cadeia obrigatoria e bloqueio sem overlay numerico |
| `REPRESENTATIVE_MAJOR_OEM` | CH570, TAC 22000, Ti10 | Ficha nominal E0 da configuracao comercial |
| `SYSTEM_TEMPLATE` | telemetria, POA, ciclo logistico | Estrutura dos dados que o cliente deve fornecer |

## Mercado de cana: o que foi possivel afirmar

Ha evidencia publica para algumas praticas e distribuicoes, mas nao foi localizada uma base publica auditavel de participacao de mercado por modelo de maquina.

### Plantio e sulcacao

- Linha simples de `1,50 m`: preset E0 nacional. O IAC a descreve como o espacamento mais usual para colheita mecanizada no [BT216](https://www.iac.sp.gov.br/publicacoes/publicacoes/iacbt126.pdf).
- Linhas duplas alternadas `0,90 x 1,50 m` e `0,40 x 1,50 m`: alternativas publicadas, nunca auto-selecionadas.
- O arranjo real precisa ser declarado por zona, ciclo e operacao, e cruzado com bitolas, hastes e colhedora.

### Variedades

O [Censo Varietal IAC 2024/25](https://www.iac.sp.gov.br/media/publicacoes/iacbt245.pdf) pesquisou `6.173.189 ha` no Centro-Sul. A lista E0 inclui:

| Variedade | Participacao observada em 2024 |
|---|---:|
| CTC4 | 12,6% |
| RB966928 | 10,7% |
| RB867515 | 10,3% |
| RB975242 | 6,0% |

Participacao observada nao recomenda uma variedade para um ambiente de producao. A escolha permanece zonal, agronomica e temporal.

### Produtividade

O segundo levantamento da [Conab 2026/27](https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/safras/safra-de-cana-de-acucar/arquivos-boletins/2o-levantamento-safra-2026-27/2o-levantamento-safra-2026-27) alimenta quatro priors E0:

| Recorte | t/ha |
|---|---:|
| Brasil | 77,905 |
| Sudeste | 80,872 |
| Centro-Oeste | 78,755 |
| Nordeste | 60,037 |

Esses valores servem para sensibilidade. Massa por tiro, ponto de enchimento, frota e POA exigem produtividade espacial ou historico calibrado por balanca.

## Conservacao do solo e agua

### Composicao dos packs

O sistema nao escolhe um cenario apenas pelo estado. A composicao e explicita e segue:

`GOVERNANCE -> DESIGN_METHOD -> DATA_PROVIDER -> CALIBRATION -> PROJECT_OVERLAY`

| Modelo | Jurisdicao | Maturidade | Pode fazer | Nao pode fazer sozinho |
|---|---|---|---|---|
| `SYS-CONSERVATION-ANA-PPA-V5-PROCESS` | Brasil | Processo | Organizar PCE, PCX, praticas e cadeia da agua | Receita numerica nacional |
| `SYS-CONSERVATION-IAC-BT216-SP-CANA` | SP | M2 codificavel | PCE, PCX, TI, TD, CEV, C1 e C2 | Aprovar solo, chuva, secao ou receptor |
| `SYS-CONSERVATION-PR-BT71-CANA-PCE` | PR | M2 codificavel | Espacamento PCE para cana, grupo 4 | PCX, secao, passabilidade ou ESD |
| `SYS-CONSERVATION-EPAGRI-HIDROTERRACO` | SC; fora com justificativa | M2/M3 | IDF, Tc, racional, Manning, TI, TD e canais | Copiar exemplos de SC para outro local |
| `SYS-CONSERVATION-UFV-TERRACO41-PARITY` | Brasil | Paridade | Testes dourados com as mesmas entradas | Ser norma ou aprovacao |
| `SYS-CONSERVATION-EMBRAPA-DOC480-FIELD-CAL` | Brasil | Protocolo | Planejar TIE/Guelph e calibracao | Copiar TIE, TR, Cd ou dimensoes do estudo |
| `SYS-CONSERVATION-EMBRAPA-DOC483-ASBUILT-OM` | Brasil | Protocolo | Secao medida, capacidade efetiva e manutencao | Copiar degradacao ou sigma do estudo |

Fontes: [ANA Volume 5](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5), [IAC BT216](https://www.iac.sp.gov.br/publicacoes/publicacoes/iacbt126.pdf), [IAPAR BT71](https://www.idrparana.pr.gov.br/system/files/publico/pesquisa/publicacoes/bt/71/bt-71.pdf), [Epagri HidroTerraco](https://publicacoes.epagri.sc.gov.br/DOC/article/view/1508), [UFV Terraco 4.1](https://gprh.ufv.br/?area=softwares), [Embrapa Doc. 480](https://www.infoteca.cnptia.embrapa.br/handle/doc/1183218) e [Embrapa Doc. 483](https://www.infoteca.cnptia.embrapa.br/handle/doc/1185689).

### Regra especifica do ESD

`SYS-CONSERVATION-ESD-PROCESS-CLOSED` e selecionavel, mas tem:

- `market_position = PROCESS_ONLY`;
- `release_ceiling = NO_PROJECT_RELEASE`;
- `automatic_numeric_solver = false`;
- `project_overlay_required = true`;
- `execution_status_without_overlay = FAIL_CLOSED`.

Para C3, o overlay precisa fechar microbacia, PCE, PCX, greide e alcance dos sulcos, convergencias, drenagem de carreadores, secoes do ESD, receptor, implantacao, as-built e manutencao. Sem isso, E2 e E3 permanecem bloqueados.

### Nunca sao defaults numericos nacionais

- familia C1, C2 ou C3;
- TI ou TD;
- grupo de solo, erodibilidade, TIE, Ksat, `C` ou `CN`;
- periodo de retorno, IDF, hietograma ou tempo de concentracao;
- espacamento definitivo de terracos;
- greide, alcance e queda admissivel de sulco;
- Manning `n`, velocidade, tensao, freeboard ou capacidade do receptor;
- secao e passabilidade de base larga;
- limiar de formacao de canal e regras de convergencia do ESD;
- degradacao, margem, sigma, ganho produtivo ou peso multicriterio.

## Solo, topografia e hidrologia

| Pacote | Padrao do sistema | O que continua local |
|---|---|---|
| Controle vertical | SIRGAS 2000, REALT2018 e hgeoHNOR2020 | Checkpoints independentes, acuracia e incerteza |
| Terreno | Fluxo LAS/LAZ/MDT | Classificacao, envelope, vazios, breaklines e mudancas |
| Solo | Prefill IBGE/Embrapa e campanha Embrapa | Perfis, textura, estrutura, erosao, TIE/Ksat e variabilidade |
| Chuva | Busca IDF no SGB | Estacao/equacao, TR, evento e aprovacao |
| Bacia | BHO6 como contexto | Divisor completo, entradas externas, exutorios e receptores |
| Drenagem | Censo de ativos | Canais, bueiros, travessias, secoes e capacidade |
| Subsuperficie | Protocolo de investigacao | Lencol, surgencias, drenos, camadas restritivas e sazonalidade |

Fontes principais: [IBGE hgeoHNOR2020](https://www.ibge.gov.br/geociencias/modelos-digitais-de-superficie/modelos-digitais-de-superficie/31283-hgeohnor2020-modeloconversaoaltitudesgeometricasgnss-datumverticalsgb.html), [IBGE Pedologia](https://www.ibge.gov.br/geociencias/informacoes-ambientais/pedologia/10871-pedologia.html), [Embrapa Manual de Metodos de Analise de Solo](https://portaldxp-p.sede.embrapa.br/busca-de-publicacoes/-/publicacao/1085209/manual-de-metodos-de-analise-de-solo), [SGB IDF](https://rigeo.sgb.gov.br/items/ca0aefe1-91de-48b4-8810-b8517907ceb1) e [ANA BHO6](https://www.gov.br/ana/pt-br/assuntos/noticias-e-eventos/noticias/base-de-dados-geoespaciais-da-ana-permite-gestao-mais-eficiente-das-bacias-hidrograficas/).

## Frota e guiamento

Os modelos abaixo sao exemplos reais de grandes fabricantes e produtos vigentes ou documentados. Nao sao ranking de vendas.

| Grupo | Modelos selecionaveis implementados |
|---|---|
| Colhedora 1 linha | John Deere CH570; Case IH Austoft 9000 |
| Colhedora 2 linhas alternadas | John Deere CH670 |
| Colhedora 2 linhas independentes | John Deere CH950 |
| Trator | John Deere 6210M |
| Transbordo | Civemasa TAC 10500 e TAC 22000 |
| Plantadora | Antoniosi PCP1102 |
| Sulcador | Civemasa SATP HD 2 hastes |
| Caminhao canavieiro | Scania G 560 6x4 XT Super |
| CVC 74 t | Randon rodotrem rebaixado 9 eixos HD |
| Guiamento | John Deere G5/SF7500; Case/Trimble; PTx GFX/NAV-900; Hexagon Ti10 |

As fichas guardam apenas nominais publicados. A composicao selecionada precisa passar pelos gates:

| Gate | Evidencia local minima |
|---|---|
| `G_ID` | Serial, versao, pneus/esteiras, bitola, eixos, engates, offsets e geometria 3D |
| `G_LOAD` | Vazio/parcial/carregado, carga por eixo, CG, pressao, estabilidade e freio |
| `G_SWEPT` | WORK/LIFT/CROSS/TURN/TRANSPORT/UNLOAD, offtracking, tailswing e envelope |
| `G_GUIDANCE` | Hardware, firmware, correcao, formato, round-trip, XTE P50/P95/MAX e latencia |
| `G_TELEM` | Velocidade, capacidade, ciclo, paradas, falhas, combustivel e custos |
| `G_ROUTE` | Largura, rampa, superficie, pontes, clearance e restricoes |
| `G_AET` | Composicao exata, PBTC/CMT, percurso e autorizacao aplicavel |

Fontes OEM: [CH570](https://www.deere.com.br/pt/colheitadeiras/colhedora-de-cana/ch570/), [CH670](https://www.deere.com.br/pt/colheitadeiras/colhedora-de-cana/ch670/), [CH950](https://www.deere.com.br/pt/colheitadeiras/colhedora-de-cana/ch950/), [Austoft 9000](https://www.caseih.com/pt-br/brasil/produtos/colhedoras-de-cana/austoft-9000/austoft-9000-1-linha-de-pneus), [6210M](https://www.deere.com.br/pt/tratores/m%C3%A9dio-cabinado-4x4/6210m-210cv/), [PCP1102](https://antoniosi.com.br/produto/pcp1102-plantadora-de-cana-automatica/), [Scania G 560](https://www.scania.com/br/pt/home/newsroom/news/2024/news-article-template-simple35.html), [Randon 74 t](https://www.randon.com.br/pt/produtos/linha-pesada/canavieiro/canavieiro-rebaixado/) e [Hexagon Ti10](https://hexagon.com/pt/products/ti10).

Para CVC acima de 74 t ate 91 t, a biblioteca permanece fechada sem composicao e AET. A [Resolucao CONTRAN 872/2021](https://www.gov.br/transportes/pt-br/assuntos/transito/conteudo-contran/resolucoes/resolucao8722021.pdf) e suas alteracoes devem ser verificadas na data e rota do projeto.

## POA e logistica

O padrao e `SYS-POA-NOT-REQUESTED-CLOSED`. O usuario pode selecionar:

- sitio existente a validar;
- POA drive-through de uma baia;
- POA drive-through multibaia;
- modelo proprio.

Nenhuma opcao preenche area, inclinacao, suporte, fila, tempo de servico ou capacidade. O POA so passa a ser candidato quando existem produtividade, frota, eventos, rede dirigivel, restricoes, drenagem e areas aptas. O dimensionamento deve comparar distancia de transbordo, fila P95, permanencia do caminhao, risco de pisoteio, capacidade do patio e distancia ate a usina.

## Valor proprio do cliente

Todo parametro configuravel aceita substituicao. O contrato minimo e:

```json
{
  "parameter_id": "agronomy.row_spacing_m",
  "value": 1.5,
  "unit": "m",
  "provenance": {
    "origin": "DECLARED",
    "source_ref": "client://crop-plan/2026",
    "captured_at": "2026-08-24T12:00:00-03:00",
    "responsible": "agronomist-id",
    "revision": "crop-plan-v1"
  }
}
```

O resolvedor valida classe, tipo, faixa, enum, unidade, origem e proveniencia. O resultado fica `CUSTOM_VALUE_PENDING_EVIDENCE_VALIDATION`: o valor prevalece sobre o prior, mas ainda precisa entrar no inventario de evidencia e nos gates de QA.

## Artefatos

| Arquivo | Funcao |
|---|---|
| `config/catalogo_modelos_reais_cana.json` | Fontes e 65 modelos versionados |
| `config/catalogo_presets_sistema.json` | Defaults e opcoes dos 26 pacotes e 10 parametros independentes |
| `config/exemplo_selecao_presets_sistema.json` | Exemplo SP com sistema, modelos OEM e valores proprios |
| `schemas/sugarcane-reference-model-catalog.schema.json` | Contrato do catalogo de fontes/modelos |
| `schemas/system-preset-catalog.schema.json` | Contrato da biblioteca de selecao |
| `schemas/project-preset-selection.schema.json` | Contrato da escolha por projeto |
| `dataset/derived/system_parameter_options.json` | Matriz gerada de 139 parametros e suas opcoes |
| `config/exemplo_resolucao_presets_e0.json` | Resolucao auditavel e anonimizada do exemplo publico |

## Uso

```powershell
python .\scripts\audit_system_presets.py
python .\scripts\resolve_project_presets.py --selection .\config\exemplo_selecao_presets_sistema.json
python -m unittest -v tests.test_system_presets
```

O fluxo futuro do sistema e:

`localizacao e objetivo -> defaults seguros -> selecao de packs/modelos -> valores proprios -> campanha de evidencias -> pedido de geracao -> gates -> cenarios -> revisao profissional -> liberacao`

O arquivo de selecao de preset nao e convertido automaticamente em pedido executivo. Essa separacao impede que um exemplo, uma ficha OEM ou um metodo regional seja promovido silenciosamente a verdade do projeto.
