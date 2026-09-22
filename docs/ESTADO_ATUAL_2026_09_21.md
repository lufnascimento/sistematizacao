# Estado atual e sequencia de entrega

Revisao consolidada em 2026-09-21, atualizada em 2026-09-22. Este documento complementa a auditoria
historica de 2026-09-14; as pendencias daquela auditoria nao devem ser lidas
como inventario atualizado sem considerar as implementacoes posteriores.

## Limite do produto

Ha uma plataforma local funcional de triagem, nao um sistema executivo completo
nem uma aplicacao comercial multiusuario. O fluxo cadastra projetos, recebe
insumos, configura parametros, congela pedidos, processa, publica alternativas,
permite revisao e entrega arquivos. O mapa integra terreno, limites, curvas e
sulcacao preliminar em planta e 3D. Nenhum desses recursos autoriza implantacao.

## Avancos desta revisao

- Resultados preservam a rodada escolhida na URL e no seletor. Abrir uma
  execucao antiga nao troca silenciosamente para a ultima execucao concluida.
- Referencia de rodada inexistente ou de outro projeto nao carrega produtos
  de uma rodada alternativa. Respostas atrasadas de resultados sao descartadas
  quando a navegacao muda.
- ZIP de entrega reune produtos por familia, manifesto, pedido congelado,
  cenarios e registros de revisao. Verifica tamanho e SHA-256 durante a copia.
  Arquivo ausente, alterado ou fora do diretorio da rodada impede a entrega.
- Nomes duplicados recebem caminhos distintos no ZIP. Nomes enviados pelo
  cliente nao podem gerar caminhos relativos fora do pacote.
- Limites locais: 2 GiB de produtos por pacote, 2.000 arquivos e duas montagens
  simultaneas. O pacote nao inclui os uploads originais nem libera guiamento.
- ZIP temporario e removido apos a resposta normal ou erro na montagem.
  Limpeza apos encerramento abrupto do servidor ainda precisa de manutencao
  programada; nao ha fila distribuida ou quota por cliente.
- Busca de arquivos e filtro por produto operam sobre a rodada selecionada.
- Rodadas de validacao de arquivos sao identificadas como verificacao de
  insumos; nao recebem o titulo de topografia publicada nem atalho para mapa.
- Falha de API ou acesso negado nao ativa demonstracao. Ha estado indisponivel
  e tentativa de reconexao. Demonstracao exige habilitacao explicita.

## Matriz funcional

| Etapa | Disponivel | Limite ou proximo aceite |
| --- | --- | --- |
| Insumos | Poligonos, MDT/MDE, LAS/LAZ e arquivos auxiliares | Upload retomavel, QA vertical independente e tratamento de grandes nuvens |
| Parametros | Valores do cliente, referencias e pedido imutavel | Completar rastreabilidade de todos os controles por produto; valores de referencia nao sao medidas locais |
| Topografia | MDT, declividade, curvas, derivados e malha de inspecao | Condicionamento hidrologico revisado, contribuicoes externas e QA independente |
| Sulcacao | Alternativas E0 e familias curvas CF0, com gates geometricos | Cobertura completa e validacao hidraulica conjunta; CF0 pode publicar somente parte dos blocos |
| Curva embutida | Triagem conceitual TI | Resolver TI/TD com secoes, espacamentos, superficie proposta, volumes e saidas |
| Base larga/passante | Especificacoes e catalogo | Solver, trafegabilidade, secoes novas/degradadas e integracao com sulcos |
| ESD | Contratos, pesquisa e verificacoes hidraulicas auxiliares | Rede espacial sulco-coletor-receptor, dimensionamento, erosao, dissipacao e saida segura |
| Rede eletrica | Ausencia explicitamente declarada ou envio de inventario | O inventario enviado ainda bloqueia sulcacao; falta converter, recortar e validar barreiras operacionais |
| Continuidade | Analises isoladas | Portais, alinhamento, estados de trabalho/deslocamento e permissoes entre talhoes/fazendas |
| Hidrologia | Chuva-excesso, hidrograma, atraso em rede, capacidade, perfil e caminhos declarados | Contribuicoes espaciais, estruturas, regimes mistos, atenuacao e propagacao do excedente |
| POA | Estudos e otimizador estatico isolado | Integrar massa por tiro, transbordos, filas, acesso, troca e compactacao |
| Resultados | Comparacao, selecao para revisao, mapa 2D/3D e historico | Perfis longitudinais/transversais, ortomosaico, nuvem progressiva e cameras sincronizadas |
| Entrega | PDF de resumo, vetores/raster originais e ZIP verificado | Atlas detalhado por cenario, filtros de pacote e contrato homologado de controlador |
| Operacao | Servidor local, fila, logs e cancelamento de processos | Autenticacao, isolamento, banco transacional, armazenamento de objetos, backups e observabilidade |

## Ordem de desenvolvimento

1. Consolidar insumos e evidencias: terreno com cobertura externa, pontos de
   controle, solo/chuva, receptores, frota e interferencias com procedencia.
   Aceite: nenhuma medida local substituida silenciosamente por um preset.
2. Publicar QA espacial das linhas: perfis, falhas de raio/greide/espacamento,
   vazios e cruzamentos localizaveis no mapa. Aceite: cada rejeicao pode ser
   rastreada a geometria, limite, valor e fonte.
3. Integrar barreiras de maquina e portais de continuidade antes de permitir
   atravessar talhoes. Rede aerea e barreira operacional, nao barragem hidraulica.
   Aceite: nenhum sulco conecta blocos atraves de uma passagem nao validada.
4. Acoplar terreno, contribuicoes e saidas aos tres metodos conservacionistas,
   um por vez. Aceite: superficie proposta, secao, balanco de agua, capacidade,
   falhas e limitacoes publicados, sem liberar metodo apenas por analogia.
5. Integrar POA e logistica aos tiros geometricamente e hidraulicamente aceitos.
   Aceite: massa, capacidade, ciclos, acessos e cenarios de troca reproduziveis.
6. Homologar produtos e operacao comercial com piloto de campo, controles de
   acesso, backup/restauracao, limites por cliente e ambiente reproduzivel.

## Evidencias de verificacao

### Alertas rastreaveis em 2026-09-22

Complemento: a selecao da linha permite baixar um JSON local de inspecao com
vertices, cotas, distancias, greides assinados e todos os intervalos em alerta.
O arquivo identifica projeto, rodada, artefato e hash declarado pelo manifesto;
nao afirma revalidacao do hash no navegador. Mantem resultado nao avaliado
quando falta referencia e nunca autoriza implantacao. Nao inclui os controles
de raio, espacamento, capacidade hidraulica ou travessias, nem entra no ZIP/PDF
oficial. Download e conteudo foram verificados em desktop/celular, com dados QA.

- Configuracao oferece `sulcation.reference_alert_grade_pct`, padrao 5%,
  apenas como referencia computacional de triagem herdada do motor E0.
  Nao e recomendacao agronomica, limite normativo ou aprovacao hidraulica.
- O pedido materializa `e0.reference_alert_grade_pct` com origem
  `E0_ASSUMPTION`. Nao preenche o parametro de autoridade tecnica
  `conservation.max_furrow_grade_pct`; esse continua sem default aprovado.
  Referencias legada e nova conflitantes impedem a resolucao no motor.
- E0 consome a referencia no calculo de triagem. As novas exportacoes E0/CF0
  preservam valor, procedencia, identificador e hash do pedido para inspecao.
  O worker verifica o hash do pedido antes de publicar as linhas.
- O perfil identifica segmentos cujo greide absoluto entre vertices excede
  a referencia, agrupa intervalos contiguos e informa comprimento afetado.
  O seletor destaca o intervalo no mapa 2D/3D sem alterar a geometria.
  O destaque 3D usa somente a malha correspondente e nao preenche lacunas.
- Ausencia de referencia ou perfil valido implica nao avaliado; ausencia
  de excedencia nao implica aprovacao. O mapa nao altera elegibilidade nem
  libera implantacao. QA de raio permanece agregado, nao espacializado.
- Catalogo atualizado: 153 parametros, 141 configuraveis. Exemplos de
  resolucao e seus hashes foram atualizados, preservando os fatos originais.

Verificacao desta etapa: 213 testes de scripts, 72 da plataforma, quatro
verificadores JavaScript e navegador desktop/celular. Foram exercitados
salvamento, pedido congelado, alerta no perfil, destaque 2D/3D e dados antigos.
Os dados de perfil desses testes sao QA, nao recalculo da fazenda real.

### Perfil longitudinal de inspecao

Novas exportacoes web de sulcacao incluem distancias acumuladas calculadas
no XY da projecao metrica de origem, preservando as cotas dos vertices.
Ao selecionar a linha, o mapa apresenta perfil, extensao amostrada, cotas,
desnivel final menos inicial e maior greide absoluto entre vertices.
Nao utiliza distancias do mapa Mercator nem alturas interpoladas da malha
visual para essas medidas. O greide entre vertices nao substitui o QA do
motor, amostragem mais densa do terreno ou validacao hidraulica.

Rodadas antigas sem distancias acumuladas mostram perfil indisponivel;
nenhum artefato da fazenda foi alterado ou recalculado nesta etapa. Perfil
e selecao foram verificados com dados QA interceptados no navegador, em
desktop e celular, incluindo troca de alternativa e dados antigos. O link
de retorno do mapa preserva agora a rodada em analise. A etapa de 2026-09-22
acrescenta alertas configurados de greide; ainda faltam falhas de raio,
espacamento, cruzamentos e criterios hidraulicos espacializados.

Na verificacao de 2026-09-21, passaram 70 testes da plataforma e
211 testes dos scripts no ambiente QGIS, incluindo contratos e geometrias.
Os verificadores de navegador exercitaram historico com duas rodadas QA,
download e reabertura, referencia invalida, celular, falha de rede, acesso
negado, reconexao, demonstracao explicita e busca/filtro dos produtos reais.

A fazenda de exemplo conserva a rodada `run_920fbee7039740b2aafc32375a6633d4`:
76 produtos, nove alternativas e PDF de sete paginas. A entrega real foi
baixada e teve os 76 hashes conferidos: 248.439.535 bytes de produtos, cerca de
120 MB compactados. O ZIP pode mudar quando novas revisoes forem registradas;
as geometrias e arquivos publicados da rodada nao sao recalculados pela entrega.

Esses testes demonstram comportamento do software, nao adequacao agronomica
ou aprovacao hidraulica da fazenda. Dados de campo e validacao profissional
continuam sendo requisitos de liberacao.
