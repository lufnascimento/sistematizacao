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

Ainda faltam malha do MDT, modo 3D, carregamento progressivo, vetores de
sulcacao e limites convertidos, perfis e comparacao de cenarios.
terrain_mesh_available permanece false. A disponibilidade de uma linha
horizontal nao significa que o terreno ou a referencia vertical existam.
