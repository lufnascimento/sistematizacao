# Propagacao preliminar na rede

## Objetivo

Este produto liga o hidrograma preliminar a uma rede declarada de trechos. Ele
responde onde a onda passa, quando o pico chega e quais contribuicoes se somam
em uma confluencia. O metodo inicial e propagacao por atraso puro, sem
atenuacao.

O HEC-HMS inclui o metodo Lag para representar translacao sem atenuacao e
separa-o de metodos que exigem armazenamento, secao e propriedades do canal.
Essa separacao sustenta a fronteira adotada aqui: o resultado e util para
organizar a rede e localizar concentracoes, mas nao para aprovar capacidade.

Fontes oficiais:

- [HEC-HMS: metodos de propagacao em canais](https://www.hec.usace.army.mil/confluence/hmsdocs/hmstr/channel-flow/available-channel-routing-methods)
- [HEC-HMS: selecao de metodo por trecho](https://www.hec.usace.army.mil/confluence/hmsdocs/hmsum/latest/reach-elements/selecting-a-reach-routing-method)
- [HEC-HMS: aplicacao de Muskingum-Cunge e insumos requeridos](https://www.hec.usace.army.mil/confluence/hmsdocs/hmsguides/applying-reach-routing-methods-within-hec-hms/applying-the-muskingum-cunge-routing-method)

## Entrada atual

O usuario habilita **Propagar a vazao pela rede**, informa o no onde o
hidrograma entra e cadastra os trechos:

```json
[
  {
    "id": "TRECHO_01",
    "upstream_node_id": "ENTRADA",
    "downstream_node_id": "JUNCAO",
    "travel_time_minutes": 10
  },
  {
    "id": "TRECHO_02",
    "upstream_node_id": "JUNCAO",
    "downstream_node_id": "SAIDA",
    "travel_time_minutes": 20
  }
]
```

Os tempos precisam ter fonte ou justificativa do projeto. A futura derivacao
pelo MDT sera uma sugestao revisavel, nunca evidencia automatica.

## Regras do grafo

- IDs de trecho sao unicos;
- nos de entrada e saida precisam ser diferentes;
- ciclos sao rejeitados;
- confluencias sao aceitas e somam vazoes no mesmo instante;
- bifurcacoes sao rejeitadas enquanto nao houver regra explicita de partilha;
- trechos sem contribuicao ficam marcados como secos;
- o volume de todas as entradas deve reconciliar com o volume das saidas.

## Produtos

- `propagacao_preliminar_rede.json`: rede, hidrograma por trecho, saidas,
  balanco e bloqueios;
- `picos_por_trecho.csv`: tempo de viagem, vazao maxima, tempo ate o pico e
  estado de cada trecho;
- resumo na plataforma: quantidade de trechos e saidas finais.

## Limite tecnico

O atraso puro nao calcula atenuacao, armazenamento, remanso, refluxo,
extravasamento, velocidade, nivel, tensao, borda livre, erosao, secao ou
caminho de falha. Tambem nao prova que a saida final e um receptor seguro.

Muskingum-Cunge ou onda cinematica so entram quando comprimento, declividade,
secao e rugosidade estiverem disponiveis e o metodo for aplicavel. Redes com
remanso importante, divisao de fluxo ou mudanca de sentido exigem modelo
hidraulico capaz de representar esses processos.

O proximo incremento e associar geometria e perfil longitudinal a cada trecho,
calcular propriedades de secao e testar capacidade nova e degradada. Essa e a
ponte para comparar curva embutida, base larga/passante e ESD por desempenho
hidraulico, alem de conservacao e operacao.
