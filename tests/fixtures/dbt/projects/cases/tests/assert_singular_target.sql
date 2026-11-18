select id
from {{ ref('singular_target') }}
where id < 0
