# Hidrograma preliminar

## O que o cliente recebe

O produto **Hidrograma preliminar** transforma a parcela da chuva que escoa em
uma serie de vazao ao longo do tempo. A plataforma entrega:

- vazao maxima estimada e tempo ate o pico;
- grafico PNG para leitura direta;
- serie CSV com tempo, vazao e volume acumulado;
- manifesto JSON com entradas, metodo, linhagem, limites e bloqueios;
- verificacao automatica de conservacao do volume.

Os nomes internos `PCX1` e `PCX2` existem somente nos contratos tecnicos e na
auditoria. Na jornada do usuario, os produtos se chamam **Chuva que vira
escoamento** e **Hidrograma preliminar**.

## Passo a passo

1. Informar area contribuinte, comportamento hidrologico do solo, fonte dos
   parametros e chuva de cada intervalo.
2. Habilitar **Calcular a parcela que escoa**.
3. Para obter vazao no tempo, habilitar **Calcular vazao ao longo do tempo** e
   informar o **Tempo de resposta da area** em minutos.
4. Selecionar os dois produtos. O hidrograma depende da chuva que vira
   escoamento e a plataforma bloqueia uma selecao incompleta.
5. Compilar o pedido imutavel e executar. Alterar chuva, area, solo ou tempo de
   resposta exige nova rodada, preservando a comparabilidade.

## Logica implementada

Cada pulso de chuva-excesso gera uma resposta triangular deslocada pelo tempo
de resposta declarado. As respostas sao somadas ponto a ponto. O pico de cada
triangulo e calculado para que sua area seja exatamente o volume do pulso. O
grid inclui inicio, pico e fim de todos os triangulos; por isso a integracao
trapezoidal reconcilia o volume de entrada mesmo quando o intervalo regular do
grafico nao coincide com esses pontos.

O fator de duracao relativa tem valor inicial editavel de `2.67`. Esse valor e
uma hipotese de triagem, nao evidencia automatica da fazenda. O tempo de
resposta tambem precisa de fonte e revisao tecnica antes de uso de projeto.

## Limite de uso

O pico publicado e uma estimativa preliminar na saida conceitual da area. O
motor ainda nao propaga a onda por carreadores, sulcos, terracos, canais ou
estruturas; nao verifica velocidade, nivel, borda livre, erosao, secao,
receptor, extravasamento nem caminho de falha. Portanto, ele nao dimensiona
curva embutida, base larga/passante ou ESD e nao autoriza guiamento de maquina.

O proximo estagio correto e associar cada area contribuinte a uma rede de
alcances, propagar o hidrograma, testar secoes novas e degradadas e validar o
receptor. So depois essa resposta hidraulica pode participar do otimizador de
sulcacao e da comparacao dimensionada entre os tres sistemas conservacionistas.
