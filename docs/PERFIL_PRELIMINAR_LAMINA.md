# Perfil preliminar da lamina

Este produto calcula um perfil unidimensional permanente e subcritico ao longo
de cada trecho prismatico trapezoidal. Ele usa a vazao maxima propagada, o
comprimento declarado, a geometria da secao, Manning, declividade e uma
profundidade conhecida a jusante.

O calculo aplica a equacao de energia pelo metodo do passo padrao. A perda por
atrito usa a media das declividades de atrito nas extremidades de cada passo.
O usuario escolhe de 2 a 1000 divisoes por trecho; 20 e o valor inicial do
sistema, sem significado de convergencia garantida.

Entradas adicionais na rede e na secao:

```json
{
  "routing_reach": {"id": "TRECHO_01", "length_m": 500.0},
  "section": {"id": "TRECHO_01", "downstream_water_depth_m": 0.55},
  "profile_step_count": 20
}
```

O motor publica `perfil_preliminar_lamina.json`,
`perfil_preliminar_lamina.csv` e `grafico_perfil_preliminar_lamina.png`.

O resultado nao cobre regime critico, supercritico ou misto, ressalto,
estruturas, mudanca de secao, confluencia por quantidade de movimento,
escoamento nao permanente, sedimento, receptor ou guiamento. Qualquer condicao
de jusante critica ou supercritica bloqueia a rodada em vez de extrapolar.

Referencia: [HEC-RAS Hydraulic Reference Manual, standard step method](https://www.hec.usace.army.mil/software/hec-ras/documentation/HEC-RAS_Hydraulic_Reference_Manual_v6.5.pdf).
