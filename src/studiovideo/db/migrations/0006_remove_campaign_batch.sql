-- Migration 0006 : retrait des parcours Campagne et Production en série ; leurs projets deviennent des
-- projets de Montage libre (médias, clips et montages conservés).
UPDATE project SET mode = 'free_edit' WHERE mode IN ('campaign', 'batch');
