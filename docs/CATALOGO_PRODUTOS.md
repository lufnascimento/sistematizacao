# Catálogo de produtos da plataforma

Status: **especificação orientada aos dados-fonte**

## 1. Papel dos PDFs recebidos

Os PDFs enviados pelo cliente são exemplos visuais das famílias de produto esperadas. Eles não são:

- verdade-terreno;
- referência quantitativa;
- fonte de cotas, limiares ou parâmetros;
- critério de aceitação;
- alvo de reprodução gráfica ou pixel a pixel.

Cada produto da plataforma deve nascer novamente dos dados-fonte, com algoritmo, versão, parâmetros, extensão espacial, resolução, incerteza e limitações registrados. O estilo da entrega pode ser configurável, mas não controla o cálculo.

## 2. Níveis de liberação

| Nível | Nome | Significado |
|---|---|---|
| L0 | Derivado do levantamento | Produto geométrico ou estatístico calculado diretamente do LAZ, MDT, MDE ou ortomosaico, ainda sujeito ao QA da fonte |
| L1 | Triagem topográfica | Indício para diagnóstico e planejamento de campo; não representa vazão, risco final ou geometria executiva |
| L2 | Anteprojeto condicionado | Alternativa calculada com chuva, solo, bacia, estruturas e operação suficientes, sujeita a revisão profissional |
| L3 | Executivo validado | Projeto revisado, vistoriado, documentado, aprovado e acompanhado da responsabilidade técnica aplicável |

Um produto não sobe de nível porque ficou visualmente parecido com um mapa anterior. Ele sobe somente quando seus gates de dados, cálculo e validação forem atendidos.

O prefixo `L` identifica o nível de liberação do produto. O prefixo `E` identifica a maturidade de um cenário (`E0_TRIAGEM`, `E1_OPERACIONAL`, `E2_CONSERVACIONISTA`, `E3_EXECUTIVO`). O prefixo `G` identifica gates. Eles não são sinônimos:

| Produto | Cenário máximo típico |
|---|---|
| L0 derivado | não contém cenário ou apenas insumo geométrico |
| L1 triagem | E0 geométrico |
| L2 anteprojeto | E1 operacional e/ou E2 conservacionista condicionado |
| L3 executivo validado | E3 aprovado |

## 3. Matriz dos produtos

| Produto | Talhão + LAZ/MDE bastam? | Nível inicial | Insumos ou gates adicionais |
|---|---:|---|---|
| Inventário e QA LAS/LAZ | Sim | L0 | Datum vertical e checkpoints para declarar acurácia absoluta |
| Densidade, espaçamento, cobertura e lacunas | Sim | L0 | Regra de qualidade compatível com o uso pretendido |
| MDT, hillshade e hipsometria | Sim, se a classe solo for confiável | L0 | Revisão de classificação, bordas, vazios e breaklines |
| MDS e altura de cultura | Não com nuvem contendo somente solo | L0 | Retornos não-solo ou nuvem original completa |
| Declividade, orientação, curvatura e perfis | Sim | L0 | Escala, janela e suavização declaradas |
| Curvas de nível | Sim | L0 | Equidistância compatível com resolução e acurácia vertical |
| Depressões, baixios e volume por cota | Sim | L1 | Comparar superfície bruta e condicionada; vistoriar feições críticas |
| Direção de fluxo e área contribuinte | Sim | L1 | Sensibilidade D8/MFD, extensão suficiente e MDT condicionado |
| Linhas preferenciais e cabeceiras candidatas | Sim | L1 | Limiar calibrado e validação de campo |
| Bacias e sub-bacias reais | Não em raster recortado no talhão | L1/L2 | Microbacia completa, exutórios, receptores e estruturas verificadas |
| Índices LS, SPI, TWI e convergência | Parcialmente | L1 | Solo, chuva, cobertura e práticas para interpretar erosão quantitativa |
| Cenário estático por cota | Sim, se a cota for informada | L1 | Datum vertical; opcionalmente conectividade e curva cota-área-volume |
| Simulação chuva-vazão | Não | L2 | IDF, TR, hietograma, solo, infiltração/CN, umidade, uso e calibração |
| Simulação hidráulica 1D/2D | Não | L2/L3 | Hidrogramas, canais, seções, rugosidade, estruturas e condições de contorno |
| Linhas candidatas de conservação e sulcação | Geometria preliminar, sim | L1/L2 | Solo, chuva, métodos regionais, receptores, máquinas, obstáculos e vistoria |
| Catálogo de interferências e quebras | Não apenas do LAZ de solo | L1/L2 | Shapes/declaracões, efeitos por operação, buffers e revisão de campo |
| Triagem topográfica de POA | Parcialmente | L1 | Área disponível, relevo, drenagem aparente e acessos candidatos |
| POA e logística capacitados | Não | L2/L3 | Produtividade, frota CTT, grafo viário, tempos, filas, solo, drenagem e permissões |
| Projeto executivo, volumes e locação | Não | L3 | Superfície aprovada, tolerâncias, memória, revisão e responsabilidade técnica |

## 4. Produtos imediatos do levantamento

### 4.1 QA da nuvem

O relatório deve registrar:

- versão LAS, formato de ponto e software gerador;
- CRS horizontal, datum vertical e unidades;
- classes, retornos, intensidade e RGB disponíveis;
- limites XYZ, densidade, espaçamento e percentis;
- lacunas, faixas, sobreposição, ruído e pontos isolados;
- distribuição espacial da densidade, sem legenda saturada;
- checkpoints, método e acurácia quando fornecidos.

Ter somente pontos classe `2` favorece a geração do MDT, mas impede auditar os pontos descartados e não comprova, por si, a qualidade da classificação.

### 4.2 Superfícies

A plataforma deve preservar versões separadas:

1. `MDT bruto`: interpolação documentada dos pontos de terreno.
2. `MDT revisado`: correções manuais e breaklines verificadas.
3. `MDT hidrocondicionado`: bueiros, canais, estradas, terraços e conectividade modelados.
4. `superfície proposta`: conservação, carreadores e sulcos candidatos incorporados.
5. `as built`: levantamento posterior à execução.

Nunca se deve sobrescrever uma superfície anterior. Todo cenário hídrico deve informar exatamente qual versão utilizou.

## 5. Fluxo e bacias

O processamento recomendado é:

1. ampliar o MDE até conter toda a área contribuinte relevante;
2. cadastrar divisores, entradas, saídas, receptores e pontos de descarga;
3. classificar estradas, bueiros, canais, terraços, valas e barreiras;
4. decidir quais feições devem bloquear, conduzir ou atravessar o fluxo;
5. executar D8 e MFD com múltiplos limiares de área contribuinte;
6. comparar superfície bruta e hidrocondicionada;
7. delimitar bacias em exutórios reais, não apenas na borda do arquivo;
8. marcar automaticamente toda bacia truncada ou dependente de contexto externo;
9. vistoriar confluências, travessias e descargas prioritárias.

O manual do [`r.watershed`](https://grass.osgeo.org/grass85/manuals/r.watershed.html) alerta que uma região que não contém toda a bacia pode subestimar a acumulação. Por isso, a área contribuinte calculada no recorte atual é triagem, não hidrologia definitiva.

## 6. Água por cota não é inundação

Uma ferramenta `MDT <= cota` pode ser oferecida quando o usuário informar a cota. O nome correto é **cenário estático por cota**. Ela pode calcular:

- área potencialmente abaixo da cota;
- volume geométrico;
- curva cota-área-volume;
- manchas conectadas a um receptor conhecido;
- sensibilidade a diferentes cotas.

Ela não representa chuva, tempo, vazão, velocidade, propagação, duração ou perigo. Portanto, nunca deve ser chamada de simulação de inundação.

Uma simulação de inundação 1D/2D exige hidrogramas ou chuva direta, terreno completo, canais e seções, rugosidade, bueiros, estradas, terraços, condições de contorno e calibração. As saídas esperadas incluem profundidade, cota d'água, velocidade, chegada, duração e perigo. O [manual 2D do HEC-RAS](https://www.hec.usace.army.mil/software/hec-ras/documentation/HEC-RAS_2D_Users_Manual_v6.5.pdf) documenta esse conjunto de entradas e resultados.

## 7. Linhas de conservação e sulcação

Depois dos gates hídricos, toda rodada compara três macrocenários obrigatórios:

- `C1_CURVA_EMBUTIDA`, abrindo variantes TI e TD;
- `C2_BASE_LARGA_PASSANTE`, abrindo variantes TI e TD e validando cada travessia;
- `C3_ESD`, abrindo variantes setorial e complementada.

Quando a elegibilidade variar espacialmente, a rodada inclui `C4_MISTO_POR_ZONA`. TI, TD, CEV, seção embutida, seção de base larga, passagem de máquina e sulcação continuam sendo objetos calculados separadamente dentro desses envelopes.

Cada candidato deve ser calculado novamente sobre a superfície proposta. A linha-guia operacional pode atravessar talhões; o trecho trabalhado e o alcance hidráulico devem ser segmentados somente em terraços, travessias, barreiras e nós de controle fisicamente classificados. Uma fronteira analítica, por si só, não autoriza término, reinício ou manobra.

No ESD, linha de controle, sulco, carreador, receptor e CEV são produtos diferentes. A semântica e os gates estão definidos em [Lógica do ESD e do Canal Escoadouro](LOGICA_ESD_E_CANAL_ESCOADOURO.md).

Os resultados topográficos atuais são seis primitivas geométricas E0 por talhão inteiro (`E0A` a `E0F`). A decomposição experimental `E0H` foi retirada: zonas podem orientar preferências locais de um campo contínuo, mas não podem cortar linhas nem criar endpoints. Essas primitivas não devem ser publicadas como se já fossem os três macrocenários conservacionistas.

A continuidade possui um gate geométrico duro: todo endpoint deve estar apoiado na borda interna de uma cabeceira ou em uma barreira classificada que exija interrupção. Endpoint interno sem suporte, cruzamento, sobreposição ou loop reprova a família. A autorização de manobra continua sendo um gate separado e exige uma superfície operacional aprovada.

Quando dois polígonos representam apenas subdivisões de uma mesma área operável autorizada e a divisa não contém carreador, obstáculo ou outra superfície física classificada, ela não é um `connector` nem um ponto de `LIFT/CROSS`. No futuro solver por bloco, essa divisa será dissolvida para gerar a família contínua; `field_id` permanece como atributo de rastreabilidade. Limite fundiário ou operacional desconhecido continua fechado até classificação e permissão.

### 7.1 Barreiras, POA e logística

Na V1, `power_line_axis` é um `LineString` informado pelo cliente pelo centro dos postes. Seu buffer, com largura e fonte registradas, é barreira absoluta: divide sulco, tiro, alcance hidráulico e rota e exclui portal, POA, fila, manobra, carga e descarga. Sem shape, somente `DECLARED_NONE` libera esse gate.

O POA publicado é um polígono operacional com acesso, baias, fila, giro, piso, drenagem e capacidade. O produto logístico inclui massa por tiro, `load_events`, `swap_windows`, rotas vazias/carregadas, utilização da frota, filas, entrega horária e mapa de intensidade do tráfego. Com LAZ e talhões apenas, o nome correto da saída é `candidato topográfico a POA`, não POA dimensionado.

A especificação está em [POA e logística de colheita](./POA_E_LOGISTICA_DE_COLHEITA.md).

## 8. Metadados obrigatórios por camada

Toda camada publicada deve trazer:

- identificador do projeto e versão;
- fonte e hash dos arquivos;
- data do levantamento e do processamento;
- CRS, datum horizontal e vertical;
- extensão, buffer, resolução e NoData;
- algoritmo, biblioteca, versão e parâmetros;
- superfície de entrada e condicionamentos aplicados;
- nível L0–L3;
- incerteza, limitações e gates abertos;
- responsável pela revisão e histórico de aprovação.

## 9. Limite de uma entrada topografica E0

Com nuvem classificada, MDT e limite de talhoes validados, podem ser gerados em E0:

- QA básico da nuvem e densidade;
- MDT preliminar, relevo e hillshade;
- declividade em graus e porcentagem;
- curvas preliminares na equidistancia configurada;
- perfis e estatísticas topográficas;
- sensibilidade de convergência e área contribuinte como L1.

Continuam bloqueados: bacias completas, chuva-vazão, inundação dinâmica, seções de estruturas, linhas finais de sulcação e projeto executivo.

## 10. Entregas

Os produtos calculados devem ser exportáveis em GeoPackage, GeoTIFF/COG, COPC/LAZ, DXF, LandXML, SHP, KML/KMZ, CSV e PDF de apresentação. O PDF é uma saída do pipeline; as camadas e os parâmetros permanecem disponíveis em formatos geoespaciais e no memorial rastreável.

## 11. Referências técnicas

- [GRASS GIS - r.watershed](https://grass.osgeo.org/grass85/manuals/r.watershed.html).
- [USACE - HEC-RAS 2D User's Manual](https://www.hec.usace.army.mil/software/hec-ras/documentation/HEC-RAS_2D_Users_Manual_v6.5.pdf).
- [ASPRS Positional Accuracy Standards 2024](https://old.asprs.org/archives/asprs-approves-edition-2-version-2-of-the-asprs-positional-accuracy-standards-for-digital-geospatial-data-2024.html).
- [IAC - Boletim Técnico 216](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf).
- [ANA - Manual do Programa Produtor de Água, volume 5](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5).
