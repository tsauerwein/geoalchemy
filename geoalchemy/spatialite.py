from sqlalchemy import select, func
from sqlalchemy.sql import and_, text, table, column 

from geoalchemy.base import SpatialComparator, PersistentSpatialElement
from geoalchemy.dialect import SpatialDialect 
from geoalchemy import functions
from geoalchemy.mysql import mysql_functions
from geoalchemy.geometry import GeometryExtensionColumn


class SQLiteComparator(SpatialComparator):
    """Comparator class used for Spatialite
    """
    def __getattr__(self, name):
        try:
            return SpatialComparator.__getattr__(self, name)
        except AttributeError:
            return getattr(sqlite_functions, name)(self)


class SQLitePersistentSpatialElement(PersistentSpatialElement):
    """Represents a Geometry value as loaded from the database."""
    
    def __init__(self, desc):
        self.desc = desc
        
    def __getattr__(self, name):
        try:
            return PersistentSpatialElement.__getattr__(self, name)
        except AttributeError:
            return getattr(sqlite_functions, name)(self)


# Functions only supported by SQLite
class sqlite_functions(mysql_functions):
    # AsSVG
    class svg(functions._base_function):
        pass
    
    # AsFGF
    class fgf(functions._base_function):
        pass
    
    # IsValid
    class is_valid(functions._base_function):
        pass
    
    @staticmethod
    def _within_distance(compiler, geom1, geom2, distance):
        if isinstance(geom1, GeometryExtensionColumn) and geom1.type.spatial_index and SQLiteSpatialDialect.supports_rtree(compiler.dialect):
            """If querying on a geometry column that also has a spatial index,
            then make use of this index.
            
            see: http://www.gaia-gis.it/spatialite/spatialite-tutorial-2.3.1.html#t8 and
            http://groups.google.com/group/spatialite-users/browse_thread/thread/34609c7a711ac92d/7688ced3f909039c?lnk=gst&q=index#f6dbc235471574db
            """
            return and_(
                        func.Distance(geom1, geom2) <= distance,
                        table(geom1.table.fullname, column("rowid")).c.rowid.in_(
                            select([table("idx_%s_%s" % (geom1.table.fullname, geom1.key), column("pkid")).c.pkid]).where(
                                and_(text('xmin') >= func.MbrMinX(geom2) - distance,
                                and_(text('xmax') <= func.MbrMaxX(geom2) + distance,
                                and_(text('ymin') >= func.MbrMinY(geom2) - distance,
                                     text('ymax') <= func.MbrMaxY(geom2) + distance)))
                                )
                            )
                        )
            
        else:
            return func.Distance(geom1, geom2) <= distance


class SQLiteSpatialDialect(SpatialDialect):
    """Implementation of SpatialDialect for SQLite."""
    
    __functions = { 
                   functions.within_distance : None,
                   functions.length : 'GLength',
                   sqlite_functions.svg : 'AsSVG',
                   sqlite_functions.fgf : 'AsFGF',
                   sqlite_functions.is_valid : 'IsValid',
                   mysql_functions.mbr_equal : 'MBREqual',
                   mysql_functions.mbr_disjoint : 'MBRDisjoint',
                   mysql_functions.mbr_intersects : 'MBRIntersects',
                   mysql_functions.mbr_touches : 'MBRTouches',
                   mysql_functions.mbr_within : 'MBRWithin',
                   mysql_functions.mbr_overlaps : 'MBROverlaps',
                   mysql_functions.mbr_contains : 'MBRContains',
                   functions._within_distance : sqlite_functions._within_distance
                   }

    def _get_function_mapping(self):
        return SQLiteSpatialDialect.__functions
    
    def get_comparator(self):
        return SQLiteComparator
    
    def process_result(self, wkb_element):
        return SQLitePersistentSpatialElement(wkb_element)
    
    def handle_ddl_before_drop(self, bind, table, column):
        if column.type.spatial_index and SQLiteSpatialDialect.supports_rtree(bind.dialect):
            bind.execute(select([func.DisableSpatialIndex(table.name, column.name)]).execution_options(autocommit=True))
            bind.execute("DROP TABLE idx_%s_%s" % (table.name, column.name));
        
        bind.execute(select([func.DiscardGeometryColumn(table.name, column.name)]).execution_options(autocommit=True))
    
    def handle_ddl_after_create(self, bind, table, column):
        bind.execute(select([func.AddGeometryColumn(table.name, 
                                                    column.name, 
                                                    column.type.srid, 
                                                    column.type.name, 
                                                    column.type.dimension,
                                                    0 if column.nullable else 1)]).execution_options(autocommit=True))
        if column.type.spatial_index and SQLiteSpatialDialect.supports_rtree(bind.dialect):
            bind.execute("SELECT CreateSpatialIndex('%s', '%s')" % (table.name, column.name))
            bind.execute("VACUUM %s" % table.name)
    
    @staticmethod  
    def supports_rtree(dialect):
        # R-Tree index is only supported since SQLite version 3.6.0
        return dialect.server_version_info[0] > 3 or (dialect.server_version_info[0] == 3 and 
                                                      dialect.server_version_info[1] <= 6)
