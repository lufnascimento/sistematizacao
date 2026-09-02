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
- distancia horizontal e diferenca vertical ate o receptor;
- cruzamentos ou aproximacoes dentro da faixa de cada barreira;
- JSON auditavel, CSV, GeoJSON 3D e mapa de triagem.

`Sem conflito detectado` significa apenas que a geometria declarada passou por essas verificacoes. Nao aprova o receptor, nao calcula sua capacidade, nao propaga o hidrograma extravasado e nao substitui levantamento completo, vistoria ou projeto executivo.

## Proximo nivel

O proximo motor deve derivar candidatos no MDT condicionado, comparar o caminho declarado com a superficie, calcular o volume que excede a secao e propaga-lo por estruturas e receptores inventariados.
