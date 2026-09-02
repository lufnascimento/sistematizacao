# Verificacao preliminar de capacidade

Este produto compara o pico propagado em cada trecho com uma secao trapezoidal
declarada pelo usuario. Para cada trecho sao informados largura de fundo,
inclinacao lateral, declividade, rugosidade de Manning e profundidade util.

O motor calcula area molhada, perimetro molhado, raio hidraulico, capacidade na
profundidade disponivel e profundidade normal requerida para o pico. Tambem
publica velocidade, numero de Froude e tensao media de contorno como
diagnosticos, sem aprovar limites erosivos.

A base matematica e a equacao de Manning para escoamento uniforme, usada pelo
[HEC-RAS para dimensionamento preliminar de canais](https://www.hec.usace.army.mil/confluence/rasdocs/ras1dtechref/6.5/stable-channel-design-functions/uniform-flow-computations).
A profundidade normal e obtida iterativamente. O proprio material do HEC-RAS
alerta que a hipotese de profundidade normal pode introduzir incerteza quando a
condicao de jusante controla o escoamento.

Exemplo de entrada:

```json
[
  {
    "id": "TRECHO_01",
    "bottom_width_m": 0.5,
    "side_slope_h_to_v": 1.5,
    "slope_m_m": 0.005,
    "manning_n": 0.04,
    "maximum_flow_depth_m": 0.6
  }
]
```

Os IDs precisam coincidir exatamente com os trechos da rede. O sistema publica
`verificacao_preliminar_capacidade.json` e `capacidade_por_trecho.csv`.

O estado **com folga** significa apenas que a vazao cabe na geometria declarada
sob escoamento permanente e uniforme. Ainda nao considera remanso, onda nao
permanente, transicoes, curvas, obstrucoes, sedimento, degradacao, borda livre,
erosao admissivel, extravasamento, caminho de falha ou seguranca do receptor.
Por isso nenhum trecho e aprovado para execucao ou guiamento nesta etapa.

O proximo incremento deve trabalhar com dois estados de secao, novo e
degradado, adicionar limites de velocidade e tensao provenientes de um pacote
tecnico regional aprovado e testar condicoes de jusante. Depois disso sera
possivel iniciar a comparacao hidraulica das estruturas de curva embutida, base
larga/passante e ESD.
