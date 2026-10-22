-- Migration 0005 : image de couverture des publications (moment choisi dans la vidéo)
ALTER TABLE master_publication ADD COLUMN cover_s REAL;
