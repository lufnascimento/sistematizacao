# Camadas por rodada

GET /api/runs/{run_id}/map-layers lista artefatos espaciais exclusivamente
da rodada e projeto pedidos. A lista preserva identificador, produto,
checksum, tamanho, referencia espacial e disponibilidade para carregamento.

O primeiro formato carregavel e GeoJSON RFC 7946 com LineString em
longitude/latitude, publicado com metadados explicitos em rodada concluida.
O cliente pode usar default_visible e default_opacity para iniciar controles
de camada. source_url aponta para o download existente do artefato.

PNG permanece uma previa, nao uma camada georreferenciada. GeoPackage,
GeoTIFF e LAS/LAZ aparecem como indisponiveis ate terem derivados web e
metadados de cena. GeoJSON antigo sem metadados nao e presumido WGS84.
Uma rodada em andamento ou que falhou nao fornece camadas carregaveis.

O visualizador map.html?run=IDENTIFICADOR ja consome essa lista. Usa Three.js
0.160.1, com arquivos locais e licenca MIT preservada em web/vendor/three.
Exibe as linhas em planta, com camera ortografica, pan, zoom, enquadramento,
visibilidade, opacidade e atributos por clique. A projecao de exibicao e
Mercator com origem local; comprimentos e quedas exibidos vem dos produtos
originais, nao da projecao da tela. Latitudes acima de 85 graus, arquivos
acima de 20 MB e camadas acima de 200 mil vertices exigem outro tratamento.

Novas rodadas topograficas publicam terrain_inspection_mesh.json quando
ha superficie suficiente. A malha amostra ate 129 por 129 vertices nos
centros dos pixels e omite cada quadrilatero que atravesse qualquer pixel
invalido do raster original, inclusive pixels entre amostras. O arquivo
registra checksum da fonte, grade, metodo e unidades. Quando nao e possivel
gerar a malha, o manifesto registra a indisponibilidade sem descartar os
demais produtos topograficos.

O modo 3D e habilitado apos carregar a malha. Permite orbitar, enquadrar,
alterar opacidade e consultar a cota interpolada da malha. Z e relativo ao
minimo da fonte para exibicao, com escala horizontal local de Mercator
compensada na vertical. Nao ha conversao de datum vertical; a cota consultada
permanece na referencia original nao informada. Cores representam a faixa
de cotas da propria rodada, nao classes de risco.

Ainda faltam carregamento progressivo, vetores de sulcacao e limites
convertidos, drapeamento das linhas, ortomosaico, perfis e comparacao de
cenarios. terrain_mesh_available reflete somente malha publicada e rodada
concluida. A presenca de linhas horizontais nao demonstra terreno ou datum.

Validacao local: python platform/tests/verify_map_viewer.py --url URL --terrain
verifica pixels, visibilidade, zoom, selecao, opacidade, rotacao e layout nos
viewports desktop e mobile. tests/test_terrain_web_mesh.py executa no Python
QGIS/GDAL e verifica cotas e uma lacuna NoData entre vertices amostrados.

## Curvas de nivel da mesma rodada

Novas execucoes topograficas tambem publicam contours_inspection.geojson.
As geometrias RFC 7946 permanecem 2D; source_elevations_m preserva a cota
original de cada vertice. O exportador exige LineString 3D com cota constante,
CRS metrico e coordenadas finitas. Nao transforma o datum vertical nem
simplifica silenciosamente: acima de 200 mil vertices registra indisponibilidade
do derivado no manifesto, mantendo o GeoPackage original.

O campo terrain_sha256 vincula as curvas ao MDT utilizado. O visualizador
carrega primeiro a malha e so usa cotas para posicionamento 3D quando esse
checksum coincide com source_sha256 da malha. Sem essa correspondencia,
a camada continua disponivel apenas em planta. Visibilidade e opacidade
sao independentes por camada. O clique diferencia curva topografica de
caminho de extravasamento e mostra a cota original, sem autorizar sulcacao.

Em planta, as linhas sao sobrepostas ao terreno para leitura. Em 3D, usam
as cotas originais e oclusao real: a simplificacao da malha pode encobrir
trechos. Drapeamento visual sobre a malha, com preservacao separada das
cotas de origem, ainda esta pendente. Limites dos talhoes e linhas de
sulcacao tambem ainda nao foram convertidos nesta etapa.

Validacao adicional: --terrain --contours no verificador de navegador
testa alteracao de pixels ao alternar curvas nos dois modos, em desktop
e mobile. tests/test_contours_web.py verifica cotas, eixo geografico,
rejeicao de linhas 2D e limite sem publicacao parcial.

Validacao da fazenda: rodada run_fbdfc41570764ba2b94335be080afd22,
com 224 curvas e malha de 28.304 triangulos. Revalidada em 2026-09-16:
48 testes da plataforma, 11 geoespaciais e verificacao visual desktop/mobile.
O ensaio de checksum divergente modifica apenas a resposta no navegador de
teste; nao altera artefatos publicados. Confere que alternar a camada nao
contorna o bloqueio de posicionamento 3D sem referencia correspondente.
