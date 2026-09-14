# Caminhos de extravasamento

## Finalidade

O produto transforma a declaracao textual de extravasamento em uma geometria 3D rastreavel. Para cada trecho, verifica se a polilinha informada desce ate o receptor associado e se cruza barreiras conhecidas.

## Insumos

- polilinha XYZ do caminho, no sentido do trecho para o receptor;
- ponto XYZ e identificador do receptor;
- relacao com o trecho cuja secao declara o mesmo receptor;
- barreiras lineares e faixa de seguranca, incluindo rede eletrica, estrada sem travessia e exclusao ambiental;
- tolerancias horizontal e vertical do levantamento.

## Verificacoes e produtos

- comprimento e queda total;
- menor declividade de segmento e maior subida adversa;
- maior subida acumulada em relacao a qualquer ponto anterior mais baixo; densificar uma subida em segmentos curtos nao evita a revisao;
- distancia horizontal e diferenca vertical ate o receptor;
- cruzamentos ou aproximacoes dentro da faixa de cada barreira;
- JSON auditavel em XYZ original, CSV, GeoJSON em longitude/latitude e mapa de triagem.

O CRS horizontal e congelado no pedido. A exportacao web usa pyproj/PROJ
com ordem longitude/latitude explicita e rejeita CRS ausente, geografico ou
com unidades diferentes de metros na entrada. As cotas originais ficam em
atributos, com datum vertical nao informado; nao sao publicadas como altura
elipsoidal WGS84. Pedidos antigos sem CRS congelado precisam ser recompilados.
Arquivos ja publicados nao sao reescritos: gere uma nova rodada para obter
o GeoJSON corrigido. A conversao nao comprova a exatidao do levantamento.

Referencias: [RFC 7946](https://www.rfc-editor.org/rfc/rfc7946.html) e
[pyproj Transformer](https://pyproj4.github.io/pyproj/stable/api/transformer.html).

`Sem conflito detectado` significa apenas que a geometria declarada passou por essas verificacoes. Nao aprova o receptor, nao calcula sua capacidade, nao propaga o hidrograma extravasado e nao substitui levantamento completo, vistoria ou projeto executivo.

## Proximo nivel

O proximo motor deve derivar candidatos no MDT condicionado, comparar o caminho declarado com a superficie, calcular o volume que excede a secao e propaga-lo por estruturas e receptores inventariados.
