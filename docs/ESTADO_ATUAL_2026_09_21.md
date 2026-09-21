# Estado atual e sequencia de entrega

Revisao consolidada em 2026-09-21. Este documento complementa a auditoria
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
