# Auditoria do produto e da plataforma

Data: 2026-09-14. Base inspecionada: bd1f65f e correcao de prontidao desta rodada.

Atualizacao apos a auditoria: implementados controle de subida acumulada,
CRS congelado no pedido, exportacao horizontal GeoJSON corrigida, API de
camadas e visualizador em planta com visibilidade, opacidade, zoom e selecao.
A verificacao visual usou uma rodada sintetica identificada como QA.
As tabelas abaixo registram a situacao inicial; terreno 3D e camadas de
sulcacao continuam pendentes. Ver CONTRATO_CAMADAS_MAPA.md.

Atualizacao seguinte: terreno 3D implementado e verificado com DEM.tif real
em nova rodada topografica. A malha e um derivado de inspecao com lacunas
NoData preservadas. Camadas de sulcacao, curvas, ortomosaico e comparacao
espacial continuam pendentes; o modo 3D ja nao e uma pendencia integral.

## Conclusao

Atualizacao de implementacao em 2026-09-16: derivados de curvas de nivel,
limites de talhoes e linhas E0/CF0 foram integrados ao pipeline e ao mapa,
com seletor de alternativa, diagnosticos separados e drapeamento visual
limitado aos triangulos validos do MDT. Tempo de manobra e limite transversal
configurados agora sao materializados no pedido do motor. A validacao da
rodada real e registrada no contrato de camadas e nos testes. Isso nao
transforma as tres familias conservacionistas especificadas em solvers completos.

Existe uma plataforma local de estudos preliminares, com cadastro, upload, configuracao, pedidos imutaveis, fila, artefatos, revisao e comparacao. Ainda nao existe o produto completo de sistematizacao conservacionista com tres familias dimensionadas e visualizacao geoespacial 3D integrada. Testes de software aprovados nao comprovam desempenho agronomico ou qualidade das linhas em campo.

Evidencias principais: platform/terraflux_api/catalog.py, models.py, jobs.py, services.py e storage.py; platform/web/app.js e api.js; config/catalogo_capacidades_motor.json e inventario_insumos_dataset_atual.json.

## Situacao por entrega

| Entrega | Situacao observada | Falta para o cliente |
| --- | --- | --- |
| Topografia | Pipeline E0 com MDT, curvas, declividade, mapas e densidade | QA vertical independente, cobertura externa, condicionamento hidrologico revisado e comparacao fonte/produto |
| Sulcacao inicial | Familias geometricas E0 e curvas continuas | Validacao hidraulica conjunta, cobertura util, falhas locais, continuidade operacional e selecao explicavel |
| Curva embutida | Triagem conceitual TI | Solver TI/TD, secao, espacamento, superficie proposta, volumes, armazenamento e descarga |
| Base larga/passante | Produto planejado | Secao nova/degradada, passabilidade da frota, travessias, estabilidade e superficie proposta |
| ESD | Contratos e logica documentados | Rede sulco-coletor-saida, contribuicoes, capacidade, distribuicao de descarga, erosao e receptores |
| Hidrologia | Chuva-excesso, hidrograma, atraso em rede, capacidade e perfil subcritico | Contribuicoes espaciais, atenuacao fisica, estruturas, regime misto, volume extravasado e receptores |
| Extravasamento | Verifica polilinhas XYZ declaradas | Derivacao pelo MDT, origem georreferenciada, cobertura de todas as saidas e propagacao do excedente |
| Rede eletrica | Declaracao de ausencia ou upload; pipeline de linhas ainda bloqueia rede enviada | Transformar shape/eixos/postes em barreiras operacionais e quebrar trabalho por perfil de maquina |
| Continuidade | Analises isoladas; pipeline E0 limita ao talhao | Portais entre talhoes/propriedades, alinhamento, raio, autorizacao, barreiras e estados trabalho/deslocamento |
| POA | Otimizador estatico isolado | Integrar tiros, massa, transbordo, capacidade, troca, manobras, compactacao, acesso do caminhao e filas |
| Relatorios | Dossie E0/CF0 e arquivos por produto | Dossie hidrologico integrado, mapas comparativos, motivos de rejeicao e reproducao dos parametros |
| Visualizacao | Imagens e downloads em resultados | Mapa comum 2D/3D, camadas, selecao de objetos, perfis, comparacao e inspecao da nuvem |
| Operacao comercial | API local e armazenamento JSON de processo unico | Usuarios, isolamento entre clientes, banco/objetos, workers, quotas, backup e restauracao testada |

## Problemas concretos encontrados

1. Prontidao do perfil sem extravasamento perdeu as checagens de comprimento e profundidade de jusante por indentacao. Corrigido nesta rodada, incluindo profundidade zero e teste de configuracoes persistidas.
2. O GeoJSON de extravasamento grava diretamente XYZ em metros, sem transformacao para longitude/latitude nem referencia espacial no arquivo. Um mapa geografico pode posiciona-lo incorretamente. Definir CRS horizontal e datum vertical no contrato; manter geometria metrica em GeoPackage e gerar GeoJSON web transformado. Nao carregar esse arquivo como WGS84 por suposicao.
3. O caminho referencia o trecho pelo identificador, mas nao verifica que seu inicio coincide com um ponto de extravasamento levantado. Adicionar ponto de origem e tolerancias XY/Z.
4. Subidas menores que a tolerancia por segmento podem se acumular. Verificar subida acumulada e sensibilidade a densificacao; trechos planos exigem avaliacao propria.
5. Barreiras declaradas na configuracao hidrologica nao equivalem ao shape enviado. Integrar assets congelados, inventario e transformacoes; diferenciar barreira de maquina de obstrucao hidraulica. Um eixo de rede aerea nao e automaticamente uma barragem para agua.
6. A ausencia de conflito e calculada somente para os caminhos informados. Mostrar tambem trechos sem caminho, receptores sem evidencia e inventario incompleto.
7. Configuracao hidrologica exige listas JSON. Substituir por tabelas editaveis, importacao vetorial e selecao no mapa, com unidades, exemplos e erros por linha.
8. Catalogo do motor PCX e inventario usam estados diferentes para a capacidade agregada. Unificar definicao de parcial, implementado e validado sem liberar familias conservacionistas por inferencia.

## Visualizador 2D/3D requerido

A experiencia principal deve ser o terreno da rodada ocupando a area de trabalho, com painel lateral de camadas e inspecao. O usuario abre Resultados, seleciona a rodada/cenario, alterna planta/3D e liga as camadas desejadas.

Camadas: MDT, ortomosaico, relevo sombreado, declividade, curvas de nivel, talhoes, sulcacao, terracos, ESD, carreadores, restricoes, rede eletrica, bacias, fluxos, saidas, caminhos excedentes e POAs. Cada camada deve indicar origem, rodada, cobertura, legenda, unidade e disponibilidade.

Controles: visibilidade, opacidade, enquadrar camada, ordem, legenda, selecao por clique, coordenadas/cota, medicao, perfil longitudinal e transversal, exagero vertical identificado e restauracao da camera. Comparar dois cenarios com cameras sincronizadas e mesmos limites de cores.

Implementacao proposta: cena Three.js com origem local para preservar precisao, malha derivada do MDT e vetores drapeados ou com Z real explicitamente identificado. Separar cota real, Z interpolado e simples deslocamento visual antissobreposicao. O exagero vertical nunca altera metricas ou exportacoes. A nuvem LAZ precisa de formato e carregamento progressivo; nao enviar a nuvem inteira ao navegador.

Contrato de cena por rodada: CRS, datum/unidade vertical, origem XYZ, extensao, resolucao, NoData, camadas, fontes/checksums, contagem original/exibida, simplificacao, limites de tamanho e estado de geracao. Raster PNG sem georreferencia nao constitui camada espacial.

Aceite: terreno real e linhas alinhados no desktop e celular; ligar/desligar muda pixels; camera gira/aproxima; selecao retorna o objeto correto; NoData nao vira terreno zero; cotas conferem com amostras do MDT; nenhuma camada cruza rodadas inadvertidamente; erro de carregamento identificado; exportacao preserva geometria original. Medir tempo de abertura, memoria e fluidez em datasets pequeno, medio e grande.

## Pendencias por prioridade

### P0: confiabilidade dos produtos

- Corrigir contrato espacial do extravasamento e adicionar origem, datum, inventario e cobertura.
- Validar geometria de linhas: espacamento minimo/maximo, intersecoes, raio, greide, comprimento util, vazios e fragmentos por talhao.
- Amostrar perfis sobre MDT e publicar locais de falha, valores medidos, limites e fontes.
- Vincular chuva/solo/bacia aos trechos e explicitar contribuicoes externas.
- Consolidar evidencia por produto: entrada, configuracao, versao do motor, checksum, unidade, metodo, cobertura e limitacoes.

### P1: fluxo de inspecao do cliente

- Publicar contrato de cena e conversor dos produtos ja existentes.
- Implementar mapa 2D/3D, camadas e comparacao de cenarios.
- Trocar JSON manual por editor de trechos, secoes, receptores e barreiras.
- Mostrar prerequisitos por produto antes de processar, com vinculo direto ao campo faltante.
- Unificar PDF, mapas, vetores, metricas e notas de revisao da mesma rodada.
- Testar criar projeto, upload, configuracao, pedido, execucao, cancelamento, retomada, resultados, revisao e download no navegador.

### P2: cenarios conservacionistas completos

- Resolver curva embutida TI/TD com superficie proposta e volume.
- Resolver base larga/passante incluindo trafegabilidade e degradacao.
- Resolver ESD com rede de contribuicoes e receptores dimensionados.
- Integrar conexoes entre talhoes e fazendas, carreadores e barreiras de energia.
- Comparar alternativas por conservacao, area plantavel, tiros uteis, manobras, horas, custo e incerteza; separar alternativas inviaveis do ranking.

### P3: desempenho operacional e implantacao

- Integrar POA ao volume por tiro e aos ciclos de transbordo/caminhao.
- Simular frota articulada, envelope varrido, bitolas, estabilidade e pisoteio.
- Calibrar tempos, velocidades e rendimentos com telemetria local.
- Homologar exportacoes por controlador e testar ida/volta, implantacao e as built.
- Preparar piloto de campo comparando previsto/observado por metodo e condicao de solo.

### P4: operacao multiusuario

- Identidade, permissoes, isolamento de projetos e trilha de revisao.
- Banco transacional, armazenamento de objetos e fila de workers geoespaciais.
- Upload retomavel, limites de recursos, progresso real e cancelamento cooperativo.
- Backup/restauracao, migracao de dados, logs e monitoramento de processamento.
- Execucao reproduzivel do ambiente GDAL/QGIS, dependencias e testes em CI.

## Insumos que o sistema pode e nao pode inferir

### Evidencia incremental em 2026-09-17

A rodada `run_920fbee7039740b2aafc32375a6633d4` concluiu o fluxo publico de
upload, configuracao, pedido e processamento dos talhoes 65918 e 65923
(46,659 ha), usando MDT de 1 m e declaracao explicita de ausencia de rede eletrica.
Publicou 76 artefatos, nove alternativas e dossie PDF de sete paginas.
Quatro alternativas E0 sao elegiveis para comparacao geometrica, duas sao
diagnosticas e as tres CF0 possuem geometria parcial. Os seis blocos aprovados
geometricamente nao constituem aprovacao da fazenda nem validacao hidraulica.

O mapa foi verificado no navegador em desktop e celular, alternando as nove
alternativas, camadas, planta/3D, zoom, rotacao e selecao. Foram verificadas a
ausencia de download inicial dos diagnosticos e a recusa de sobreposicao 3D
quando a origem do terreno nao corresponde. A malha tem aproximadamente
27.800 triangulos, com 767 curvas de nivel e 140 aneis de limites e ilhas;
esses aneis nao representam 140 talhoes. Ha 33 camadas espaciais publicadas.

Testes locais: 56 testes da plataforma, 16 testes geoespaciais, 15 testes de
pedido/dossie e testes JavaScript de interpolacao e lacunas da malha aprovados.
O PDF foi aberto e a pagina de comparacao visual inspecionada. Ele e um dossie
de resumo com mapas, nao um atlas vetorial detalhado de cada alternativa.

Atualizacao posterior na mesma data: a suavizacao foi corrigida com o novo
parametro `terrain.smoothing_sigma_m`, ligado ao sigma gaussiano usado pelos
motores E0/CF0. Padrao efetivo de 4 m, zero desliga o filtro. O campo antigo
de raio foi retirado da tela e preservado apenas como legado sem efeito, sem
conversao implicita. Testes cobrem configuracao, pedido imutavel, comando do
worker, contrato do motor e efeito numerico, preservando fonte e mascara NoData.
A rodada e o PDF citados acima nao foram recalculados por essa alteracao.
Regressao desta correcao: 61 testes da plataforma, 42 de presets/pedido/dossie
e cinco de cobertura/suavizacao do terreno aprovados. O navegador verificou
salvar e reabrir sigma zero e 2,5 m, congelar ambos em pedidos separados e
mostrar o formulario em desktop/celular, sem iniciar processamento da fazenda.

Tempo de manobra e declividade lateral configurados agora chegam ao pedido do
motor, com teste de regressao. A rodada acima antecede essa correcao; ela nao
comprova o uso desses dois overrides. Na auditoria inicial desta rodada, o raio
de suavizacao ainda nao tinha semantica alinhada ao sigma; a correcao e seus
limites estao registrados na atualizacao posterior acima.

As listas anteriores registram a auditoria original e nao sao uma declaracao
de que tudo permanece ausente. Inspecao 2D/3D, derivados vetoriais, logs ao vivo
e cancelamento da arvore de processos avancaram nesta rodada. Permanecem
pendentes os tres metodos conservacionistas completos, continuidade entre
talhoes/propriedades, corte por rede eletrica, POA dinamico, atlas detalhado,
validacao de campo e operacao multiusuario. CI geoespacial foi configurada;
resultado remoto ainda deve ser conferido apos publicacao.

Poligonos e nuvem de solo classificada permitem geometria e derivados topograficos conforme cobertura e qualidade. Ortoimagem isolada nao fornece terreno. Chuva de projeto, propriedades hidraulicas do solo, estrutura de travessias, condicao de receptor, frota e permissao de passagem precisam de fonte declarada. Modelos de referencia devem ser selecionaveis com procedencia e possibilidade de sobrescrita, sem preencher medidas locais como se fossem observadas.

O estado funcional final exige que um projeto novo atravesse todo o fluxo com seus proprios dados, produza alternativas validas para os metodos solicitados, permita inspecao espacial e explique as diferencas. A plataforma atual ainda nao atende a esse criterio completo.
