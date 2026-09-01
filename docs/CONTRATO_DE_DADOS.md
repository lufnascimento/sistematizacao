# Contrato de Dados e Levantamento

Este documento define o que o cliente pode enviar, como o TerraFlux interpreta cada arquivo e quais ausências bloqueiam cada nível de resultado.

## 1. Rotas de entrada

### Rota preferida: MDT pronto e auditável

- Polígono dos talhões.
- MDT em GeoTIFF/COG.
- Ortomosaico para conferência.
- Relatório do levantamento e checkpoints.
- Vetores de estruturas e restrições.

É a rota de menor custo computacional e menor ambiguidade, desde que o MDT represente realmente o solo.

### Rota completa: nuvem de pontos

- `LAS`, `LAZ` ou `COPC` com X/Y/Z.
- Classes LAS preservadas quando disponíveis.
- Polígono e ortomosaico.
- Relatório de voo, controle e checagem.

O TerraFlux normaliza para COPC, audita a classificação e deriva o MDT.

### Rota de triagem: ortomosaico

- GeoTIFF/COG RGB.
- Polígono com CRS.

Permite fotointerpretação e planejamento do levantamento. Não habilita declividade, vazão, corte/aterro, terraços dimensionados ou linhas executivas.

### Fotos brutas

Não fazem parte do MVP. Quando aceitas no futuro, devem ser processadas por WebODM, Pix4D, Metashape ou provedor especializado. O produto não deve construir um motor de fotogrametria próprio.

## 2. Matriz de insumos

| Grupo | Insumo | Triagem | Anteprojeto | Executivo |
|---|---|---:|---:|---:|
| Geometria | Polígono válido e localização | Obrigatório | Obrigatório | Obrigatório |
| Elevação | MDT de terreno | Público/baixa confiança | Obrigatório | Obrigatório e aprovado |
| Elevação | Nuvem de pontos | Opcional | Opcional se MDT existir | Obrigatória para auditoria ou relatório equivalente |
| Imagem | Ortomosaico | Recomendado | Recomendado | Recomendado |
| Precisão | GCPs e checkpoints independentes | Não | Recomendado | Obrigatório |
| Precisão | RMSE/resíduos e método | Não | Recomendado | Obrigatório |
| Referência | CRS horizontal, vertical, unidades e época | Obrigatório | Obrigatório | Obrigatório |
| Contexto | Área contribuinte a montante e destino a jusante | Aproximado | Obrigatório | Obrigatório e vistoriado |
| Fundiário | Fazenda, unidade legal, talhão e classe dos limites | Recomendado | Obrigatório para conexão | Obrigatório e conferido |
| Solo | Classe e perfil/horizontes | Público | Obrigatório | Obrigatório e amostrado |
| Solo | Textura, estrutura, profundidade, matéria orgânica | Não | Recomendado | Obrigatório |
| Solo | Infiltração/condutividade representativa | Não | Faixa inferida | Obrigatório para infiltração/retenção |
| Solo | Compactação e hidromorfia | Não | Recomendado | Obrigatório quando aplicável |
| Chuva | IDF, duração, TR e hietograma | Não | Obrigatório | Obrigatório e justificado |
| Manejo | Palha, preparo, cobertura, reforma e tráfego | Não | Obrigatório | Obrigatório |
| Estruturas | Estradas, bueiros, canais, terraços e saídas | Visíveis | Obrigatório | Obrigatório e vistoriado |
| Interferências | Declaração de completude e catálogo de obstáculos | Não revisado | Obrigatório para linhas/POA | Obrigatório e vistoriado |
| Energia | Eixo da rede pelo centro dos postes (`LineString`) ou declaração sem rede | Recomendado | Obrigatório para conexões e POA | Obrigatório e conferido |
| Ambiente | Cursos, nascentes, APPs, áreas úmidas e exclusões | Público | Obrigatório | Obrigatório e conferido |
| Operação | Espaçamento, implementos, bitola e cabeceira | Não | Obrigatório para linhas | Obrigatório e validado |
| Operação | Raio mínimo da trajetória durante trabalho (`fleet.minimum_work_path_radius_m`) | Não | Obrigatório para validar a linha | Obrigatório e validado por conjunto |
| Operação | Raio mínimo de giro/manobra (`fleet.minimum_turn_radius_m`) | Não | Obrigatório para rotas e cabeceiras | Obrigatório e validado por conjunto |
| Operação | Portais, corredores e permissões de trânsito/trabalho | Não | Obrigatório para multi-talhão | Obrigatório e vigente |
| Logística | Carreadores/estradas dirigíveis, sentidos, piso, carga e acessos | Não | Obrigatório para rotas/POA | Obrigatório e vistoriado |
| Colheita | Produtividade, sequência, turnos e meta de entrega | Não | Obrigatório para dimensionar POA | Obrigatório e calibrado |
| Frota CTT | Colhedoras, transbordos e caminhões, capacidades, dimensões e tempos | Não | Obrigatório para dimensionar POA | Obrigatório e validado |
| POA | Pátios existentes, áreas candidatas/proibidas, baias e permissões | Não | Obrigatório para otimização | Obrigatório e aprovado |
| Campo | Erosões, alagamentos, surgências e fotos | Opcional | Recomendado | Obrigatório |
| Responsabilidade | Revisor e profissional habilitado | Não | Recomendado | Obrigatório |

## 3. Formatos aceitos

| Tipo | Preferido | Alternativas | Observações |
|---|---|---|---|
| Vetor | GeoPackage | GeoJSON, SHP ZIP, KML/KMZ, DXF | GeoPackage evita o conjunto frágil do shapefile |
| Raster | COG | GeoTIFF | Deve possuir geotransform, CRS e NoData |
| Nuvem | COPC | LAZ, LAS | COPC permite leitura parcial pela web |
| Tabela | CSV UTF-8 | XLSX, JSON | Colunas, unidades e separador devem ser declarados |
| Relatório | PDF/A | PDF | Relatório de voo, QA, pontos e metodologia |
| Fotos de campo | JPEG/HEIC | PNG | Data, coordenada e orientação quando disponíveis |

Arquivos SHP devem ser enviados em ZIP contendo pelo menos `.shp`, `.shx`, `.dbf` e `.prj`. Nomes internos não podem conter caminhos absolutos ou `..`.

## 4. Manifesto do levantamento

Todo upload técnico deve ser acompanhado por formulário ou `manifest.json` equivalente:

```json
{
  "project": "fazenda-santa-helena-bloco-norte",
  "survey_date": "2026-07-28",
  "method": "uas_photogrammetry_rtk",
  "surface_condition": "solo_exposto_pos_colheita",
  "horizontal_crs": "EPSG:31982",
  "vertical_reference": "REALT-2018_altitude_normal",
  "vertical_unit": "m",
  "geoid_conversion": "hgeoHNOR2020",
  "gsd_cm": 3.2,
  "point_density_per_m2": 85,
  "ground_classification": "LAS_class_2",
  "checkpoint_count": 30,
  "rmse_xy_cm": 3.8,
  "rmse_z_cm": 4.6,
  "upstream_coverage": "complete",
  "downstream_outlet": "surveyed",
  "responsible": "empresa/profissional",
  "report_file": "relatorio_qa.pdf"
}
```

Os números acima são apenas exemplo de formato, não aceitação automática.

## 5. Requisitos do levantamento com VANT

### Planejamento

- Voar preferencialmente após colheita/preparo, com solo visível.
- Evitar cana adulta, vegetação densa e condições que escondam o terreno.
- Cobrir a área contribuinte, entradas de água e uma faixa suficiente a jusante.
- Incluir estradas, bueiros, terraços existentes, canais e pontos de descarga.
- Definir GSD e precisão pelo menor desnível relevante do projeto, não pela estética da imagem.
- Planejar sobreposição, altura, velocidade e câmera conforme relevo e método fotogramétrico.
- Manter registro de base RTK/PPK, efemérides, calibração e condições de voo.

### Controle e checagem

- GCP não é checkpoint.
- Checkpoints devem ser independentes e distribuídos em área, geometria e relevo.
- Registrar coordenadas, método, incerteza e resíduos individuais.
- Reportar RMSE e também percentis/outliers; uma média sozinha pode ocultar problemas locais.
- Conferir separadamente superfícies não vegetadas e vegetadas.

O Manual do Incra exige avaliação de acurácia absoluta, GSD compatível, pontos independentes e RMS para produtos aerofotogramétricos. O padrão ASPRS 2024 traz addenda específicos para UAS, fotogrametria e LiDAR e elevou o mínimo geral de checkpoints de avaliação para 30. [Incra](https://www.gov.br/incra/pt-br/assuntos/governanca-fundiaria/Manual_Tecnico_de_Georreferenciamento_2_Edicao.pdf/%40%40display-file/file), [ASPRS 2024](https://old.asprs.org/archives/asprs-approves-edition-2-version-2-of-the-asprs-positional-accuracy-standards-for-digital-geospatial-data-2024.html)

### Operação legal do voo

Quando o TerraFlux ou parceiro executar o levantamento, devem ser verificados cadastro, homologação, seguro e acesso ao espaço aéreo aplicáveis. O DECEA informa que operações profissionais exigem solicitação/autorização no SARPAS e que a ICA 100-40/2026 rege o acesso ao espaço aéreo. [Portal DRONE/UAS do DECEA](https://www.decea.mil.br/drone/)

## 6. Gates automatizados

### Gate 0 — segurança

- Assinatura real do arquivo, não apenas extensão.
- Limite de bytes, pontos, pixels e descompressão.
- Antimalware.
- Proteção contra zip bomb e path traversal.
- Processamento em contêiner sem rede e com quotas.

### Gate 1 — referência espacial

Bloquear quando:

- CRS estiver ausente.
- Unidade não for métrica no processamento.
- Coordenadas estiverem fora do local declarado.
- Datum vertical não estiver informado.
- Altitude geométrica for usada como normal sem transformação.
- Polígono e levantamento não se sobrepuserem.

### Gate 2 — cobertura

- Exigir 100% da área útil dentro do footprint do MDT.
- Exigir 100% das células do MDT que intersectam a área útil com valor válido, sem NoData ou vazios internos.
- Cobertura de entradas e saídas.
- Fluxo de dado público que cruza a borda.
- Distância da borda ao divisor provável.
- Alerta “talhão não contém toda a área contribuinte”.

Qualquer área útil fora do footprint ou sobre célula inválida bloqueia a geração; o motor não pode preencher, extrapolar ou limitar coordenadas silenciosamente.

### Gate 3 — nuvem e terreno

- Densidade total e densidade de pontos de solo.
- Distribuição espacial, não apenas média.
- Classes LAS e origem da classificação.
- Ruído, duplicatas, faixas, degraus e outliers.
- Diferença MDS-MDT.
- Distância de cada célula ao ponto de solo observado.
- Perfis automáticos em estradas, canais e talvegues.
- RMSE e resíduos nos checkpoints.
- Mapa de confiança do MDT.

### Gate 4 — hidrologia

- Entradas e saídas conhecidas.
- Depressões classificadas como reais, artefatos ou pendentes.
- Bueiros e travessias representados.
- Sensibilidade a resolução e método de fluxo.
- Balanço de massa.
- Parâmetros de chuva/solo com fonte.

### Gate 5 — projeto e máquina

- Todas as restrições duras da geometria.
- `fleet.minimum_work_path_radius_m` aplicado à curvatura da linha com o implemento em trabalho.
- `fleet.minimum_turn_radius_m` aplicado separadamente a retornos, cabeceiras e manobras; ele não substitui o raio da trajetória em trabalho.
- Perfis dentro dos limites aprovados.
- Saídas protegidas.
- Sem bloqueadores ambientais.
- Formato e CRS aceitos pelo equipamento.
- Revisão e aprovação correspondentes ao nível de entrega.

## 7. Classes provisórias de prontidão

Estas bandas são hipóteses do piloto, não normas agronômicas universais:

| Classe | Condição | Uso máximo |
|---|---|---|
| D — desconhecida | Sem precisão ou datum demonstrado | Triagem visual |
| C — regional | MDE público ou elevação grosseira | Planejamento de levantamento |
| B — planejamento | MDT com QA e erro compatível com análises de relevo | Anteprojeto |
| A — locação | Checkpoints independentes e erro compatível com a menor queda de projeto | Candidato a executivo |

Para a classe A, o limite vertical deve ser definido pelo responsável. Ponto de partida para teste: `RMSEz <= min(5 cm, 1/3 da menor queda vertical crítica)`. Para planejamento, pode-se testar `RMSEz <= 10 cm`. Esses valores precisam ser calibrados nos casos reais e não dispensam análise dos resíduos locais.

Resolução de pixel não é precisão. Um MDT de 5 cm por pixel não possui automaticamente 5 cm de exatidão vertical.

## 8. Escopo fundiário, portais e autorizações

Toda geometria operável deve obedecer à hierarquia:

```text
farm_id -> legal_land_unit_id -> field_id
```

`owner_ref` e `operator_ref` serão identificadores opacos; o cenário não precisa carregar dados pessoais. Cada fronteira será classificada como `INTERNAL_FIELD`, `PRIVATE_CARRIER`, `LEGAL_BOUNDARY`, `PUBLIC_ROAD`, `WATERCOURSE`, `APP` ou `UNKNOWN`. A política padrão é fechada: um limite `UNKNOWN` não pode receber conexão automática.

Um portal entre zonas ou talhões precisa registrar:

- corredor `LineStringZ` e perfil transversal/longitudinal;
- largura útil, degraus, cristas, depressões e capacidade de suporte;
- sarjetas, bueiros, valas, terraços, canais e caminho da água;
- fazenda, unidade legal e talhão em cada lado;
- estado operacional `BLOCKED`, `LIFT_CROSS` ou `WORK_THROUGH`;
- estado hidráulico `INTERRUPTED`, `CONTROLLED_TRANSFER`, `CONTINUOUS_VERIFIED` ou `UNRESOLVED`;
- evidência, vigência, condições e responsável pela autorização.

As permissões são independentes e versionadas: `SURVEY`, `DESIGN`, `TRANSIT`, `WORK`, `EARTHWORK` e `WATER_TRANSFER`. `GUIA_CONTINUA` exige ao menos `TRANSIT`; `TRABALHO_CONTINUO` exige `TRANSIT` e `WORK`; remodelagem exige `EARTHWORK`; transferência de água entre unidades legais exige `WATER_TRANSFER`.

Uma única nuvem de pontos cobrindo duas fazendas demonstra que o terreno foi levantado, não que existe permissão para cruzar a divisa. Mesmo quando o proprietário é o mesmo, devem ser confirmados operador, arrendamento, safra, variedade, espaçamento, janela de operação e frota.

### 8.1 Rede elétrica como barreira na V1

O upload mínimo é um `LineString` ou `MultiLineString` traçado pelo centro dos postes. A geometria será armazenada como `POWER_LINE_AXIS`: ela representa o eixo horizontal aproximado da rede e não a posição tridimensional dos cabos.

O arquivo deve trazer ou ser acompanhado por:

- `feature_id`, método, data, fonte e precisão horizontal;
- largura total ou meia largura da faixa de exclusão e respectiva fonte;
- concessionária/proprietário e situação da revisão;
- escopo espacial coberto pela declaração.

Na V1, o buffer dessa linha é barreira absoluta. Ele recorta a área cultivável, divide os sulcos físicos, divide o tiro operacional e impede portal, POA, fila, carregamento, descarga e manobra. Não existe travessia automática com implemento levantado. Qualquer travessia futura exigirá outro nível de dados, envelope 3D da frota e procedimento aprovado.

A largura da faixa não possui default universal. Ela deve vir do cadastro/regra aplicável e sua incerteza; o usuário pode ampliar a margem, mas não reduzi-la abaixo do mínimo registrado.

O inventário de energia usa `PROVIDED`, `DECLARED_NONE` ou `NOT_REVIEWED`. O catálogo geral de interferências usa `COMPLETE`, `PARTIAL` ou `NOT_REVIEWED`. Um shape parcial não libera o restante da área; somente `DECLARED_NONE` significa ausência declarada de rede.

Ausência do shape não equivale a ausência de energia. Nuvem classificada somente como solo, como o LAZ atual, não contém evidência suficiente de postes ou cabos.

### 8.2 Catálogo geral de restrições

Além da energia, devem ser aceitas camadas de cercas/porteiras, rodovias, ferrovias, dutos e redes enterradas, pivôs e irrigação, edificações, árvores/rochas, ravinas/voçorocas, áreas úmidas, solos de baixa capacidade, cursos d'água/APP, terraços/canais/CEV, pontes/bueiros e corredores de emergência.

Cada feição informa um efeito por operação: plantio, colheita, trânsito, movimentação de terra, hidráulica e POA. Os efeitos possíveis incluem `EXCLUDE`, `SPLIT_WORK`, `BLOCK_ROUTE`, `TRANSIT_ONLY`, `CROSS_AT_PORTAL`, `NO_STOP`, `NO_TURN`, `NO_LOAD`, `NO_EARTHWORK`, `HYDRAULIC_NODE`, `SEASONAL`, `LOAD_LIMITED` e `REVIEW_REQUIRED`.

### 8.3 Dados do POA e da logística

O POA não é apenas um ponto. O insumo deve distinguir o pátio físico, acessos, baias de transferência, filas, áreas de giro e sentidos de circulação. Para dimensioná-lo, também são necessários:

- superfície de produtividade ou P10/P50/P90 por talhão;
- sequência e janela de colheita;
- rede dirigível de linhas de tráfego, cabeceiras, portais, carreadores e estradas;
- velocidades vazio/carregado, capacidades, tempos, disponibilidade e falhas da frota;
- rotas/destinos dos caminhões e meta horária de entrega;
- capacidade de suporte, condição seca/úmida, drenagem e autorizações dos locais.

Com polígono e LAZ, o produto pode apenas triar candidatos topográficos a POA. Sem produtividade, frota, rede viária e tempos, não pode afirmar capacidade, fila, custo ou ganho logístico.

## 9. Dados públicos e inferências

O sistema pode buscar:

- IDF do Atlas Pluviométrico do SGB.
- Séries do Hidroweb/ANA.
- Solo do PronaSolos/Embrapa.
- Hidrografia, CAR e camadas públicas aplicáveis.
- MDE regional para detectar contexto externo.

Esses dados devem aparecer como **inferidos/externos**, com escala, licença, data e limitações. O mapa nacional de solos em escala 1:5.000.000, por exemplo, é inadequado para prescrição detalhada de talhão. [PronaSolos](https://www.embrapa.br/en/web/solos/busca-de-solucoes-tecnologicas/-/produto-servico/9076/portal-de-dados-da-plataforma-tecnologica-pronasolos-em-ambiente-sigweb)

## 10. Motivos de rejeição amigáveis

O upload não deve retornar “arquivo inválido” sem contexto. Exemplos:

- “O ortomosaico não possui banda de elevação. Ele será usado apenas como imagem.”
- “A nuvem tem poucos pontos classificados como solo no setor sul.”
- “A referência vertical não foi informada; não é possível comparar cotas.”
- “Há uma área contribuinte entrando pelo limite norte, fora do levantamento.”
- “A estrada aparece como barragem porque nenhum bueiro foi cadastrado.”
- “O erro vertical medido é maior que a queda da estrutura proposta.”
- “Não existe destino seguro cadastrado para a descarga.”
- “A divisa está na nuvem, mas não há corredor nem autorização de trânsito para conectar os talhões.”
- “As linhas podem ser pareadas, porém o portal não comporta o envelope da frota informado.”
- “A camada de energia não foi enviada nem declarada como inexistente; tiros conectados e POAs permanecem bloqueados.”
- “O eixo da rede cruza esta família; os sulcos e a rota foram quebrados nos limites da faixa de exclusão.”
- “O local parece topograficamente adequado a um POA, mas faltam produtividade, frota e rede viária para dimensioná-lo.”

Cada motivo deve indicar: impacto, mapa do problema, ação corretiva e nível ainda permitido.

## 11. Checklist de campo executivo

- Confirmar erosões, ravinas, voçorocas e alagamentos.
- Confirmar tipo e condição de cada bueiro.
- Confirmar entradas externas e saídas.
- Abrir e descrever perfis de solo representativos.
- Medir infiltração quando a prática depender dela.
- Identificar compactação e hidromorfia.
- Conferir APPs, cursos, nascentes e áreas úmidas.
- Conferir obstáculos e interferências não visíveis.
- Levantar o eixo de redes elétricas pelo centro dos postes, a faixa aplicável e a declaração de completude.
- Verificar acessibilidade das máquinas.
- Levantar largura, perfil, drenagem e capacidade de suporte dos portais.
- Confirmar limites, operador e permissões necessárias em cada travessia.
- Conferir locais de POA, acesso de caminhões, giro, filas, suporte do piso e drenagem.
- Fotografar pontos críticos com coordenada.
- Materializar referências de locação.
- Registrar responsável, data e condição de umidade.
